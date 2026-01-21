import os, json, requests, random, smtplib, ssl, unicodedata
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from datetime import datetime
from gen_ai_hub.proxy.langchain.init_models import init_llm
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from langgraph.graph import START, StateGraph, MessagesState
from langgraph.prebuilt import tools_condition, ToolNode
from flask import Flask, request, jsonify, abort
from hdbcli import dbapi
from typing import TypedDict, Optional


SESSION_STORE = {}
class AgentState(MessagesState):
    pending_question: Optional[str]
    last_user_question: Optional[str]

ADMIN_NAME = "Administrador"
ADMIN_EMAIL = os.getenv("ADMIN_NOTIFICATION_EMAIL")

app = Flask(__name__)
# Port number is required to fetch from env variable
# http://docs.cloudfoundry.org/devguide/deploy-apps/environment-variable.html#PORT
cf_port = os.getenv("PORT")

SIMILARITY_THRESHOLD = 0.72

# Credentials for SAP AI Core need to be set as environment variables in the manifest.yml file
# AICORE_AUTH_URL
# AICORE_BASE_URL
# AICORE_CLIENT_ID
# AICORE_CLIENT_SECRET
# AICORE_RESOURCE_GROUP

# Credentials for the SMTP server to send emails, hardcoded for testing
smtp_server   = "sandbox.smtp.mailtrap.io"
smtp_port     = 587
smtp_user     = os.getenv("MAILTRAP_SMTP_USER")
smtp_password = os.getenv("MAILTRAP_SMTP_PASS")

def normalize_answer(text: str) -> str:
    text = text.strip().lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text


def is_affirmative(text: str) -> bool:
    value = normalize_answer(text)
    return value in {"y", "yes", "si", "s"}

def is_negative(text: str) -> bool:
    value = normalize_answer(text)
    return value in {"n", "no"}


def get_hana_connection():
    return dbapi.connect(
        address=os.getenv("SAP_HANA_CLOUD_ADDRESS"),
        port=int(os.getenv("SAP_HANA_CLOUD_PORT")),
        user=os.getenv("SAP_HANA_CLOUD_USER"),
        password=os.getenv("SAP_HANA_CLOUD_PASSWORD"),
        encrypt=True,
        sslValidateCertificate=False
    )

#############################
# Provide the tools / functions for the AI agent

def get_user_role():
    role = request.headers.get("X-User-Role", "USER")
    return role.upper()

def require_admin():
    if get_user_role() != "ADMIN":
        abort(403, "Admin privileges required")


def test_hana_connection():
    try:
        conn = dbapi.connect(
            address=os.getenv("SAP_HANA_CLOUD_ADDRESS"),
            port=int(os.getenv("SAP_HANA_CLOUD_PORT")),
            user=os.getenv("SAP_HANA_CLOUD_USER"),
            password=os.getenv("SAP_HANA_CLOUD_PASSWORD"),
            encrypt=True,
            sslValidateCertificate=False
        )

        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM DUMMY")
        row = cursor.fetchone()
        result = row[0] if row else None

        cursor.close()
        conn.close()

        return {"hana_connection": "OK", "result": result}

    except Exception as e:
        return {"hana_connection": "ERROR", "details": str(e)}

@app.route("/health/hana")
def hana_health():
    return jsonify(test_hana_connection())

#CRUD EMAL

def create_pending_question(question: str, created_by="USER") -> str:
    conn = get_hana_connection()
    cursor = conn.cursor()

    sql = """
        INSERT INTO CHATBOT_FAQ_QUESTIONS
        (AID, QUESTION, STATUS, CREATED_AT, CREATED_BY)
        VALUES (
            CHATBOT_FAQ_AID_SEQ.NEXTVAL,
            ?, 'PENDING', CURRENT_TIMESTAMP, ?
        )
    """

    cursor.execute(sql, (question, created_by))
    conn.commit()
# ===== NUEVO (mínimo indispensable) =====
    try:
        admin_name = "Administrador"
        admin_email = get_email_address(admin_name)

        email_text = f"""
Se ha registrado una nueva pregunta en estado PENDING.

Pregunta:
"{question}"

Creada por: {created_by}

Por favor, ingrese al sistema para revisarla.
""".strip()

        result = send_email(
            recipient_name=admin_name,
            email_address=admin_email,
            email_text=email_text
        )

        print(f"[EMAIL] {result}")

    except Exception as e:
        print(f"[ERROR] Admin notification email failed: {e}")
    # ===== FIN NUEVO =====
    cursor.close()
    conn.close()

    return "Your question has been registered and is pending review."


def list_pending_questions():
    conn = get_hana_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AID, QUESTION, CREATED_AT, CREATED_BY
        FROM CHATBOT_FAQ_QUESTIONS
        WHERE STATUS = 'PENDING'
        ORDER BY CREATED_AT
    """)

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return [
        {
            "aid": r[0],
            "question": r[1],
            "created_at": r[2].strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "created_by": r[3]
        }
        for r in rows
    ]


def answer_question(aid, answer_text):
    conn = get_hana_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT STATUS
        FROM CHATBOT_FAQ_QUESTIONS
        WHERE AID = ?
    """, (aid,))
    row = cursor.fetchone()

    if not row:
        return {"error": "Question not found"}

    # 1️⃣ Insert / replace answer
    cursor.execute(
        "DELETE FROM CHATBOT_FAQ_ANSWERS WHERE AID = ?",
        (aid,)
    )

    cursor.execute(
        "INSERT INTO CHATBOT_FAQ_ANSWERS (AID, ANSWER) VALUES (?, ?)",
        (aid, answer_text)
    )

    cursor.execute(
    "SELECT QUESTION FROM CHATBOT_FAQ_QUESTIONS WHERE AID = ?",
    (aid,)
    )
    question_text = cursor.fetchone()[0]

    translated_question = (
        translate_to_english(question_text)
        if needs_translation(question_text)
        else question_text
    )


    # 2️⃣ Activar pregunta
    cursor.execute("""
        UPDATE CHATBOT_FAQ_QUESTIONS
        SET
            QUESTION_VECTOR = VECTOR_EMBEDDING(
                ?,
                'DOCUMENT',
                'SAP_NEB.20240715'
            ),
            STATUS = 'ACTIVE'
        WHERE AID = ?
    """, (translated_question, aid))


    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "answered", "aid": aid}

    


def delete_question(aid: int):
    conn = get_hana_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE CHATBOT_FAQ_QUESTIONS
        SET STATUS = 'DELETED'
        WHERE AID = ?
    """, (aid,))

    conn.commit()
    cursor.close()
    conn.close()

    return f"Question {aid} marked as DELETED."


def update_question(aid: int, new_question: str):
    conn = get_hana_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT STATUS
        FROM CHATBOT_FAQ_QUESTIONS
        WHERE AID = ?
    """, (aid,))

    row = cursor.fetchone()
    if not row:
        return {"error": "Question not found"}

    if row[0] != "PENDING":
        return {"error": "Only PENDING questions can be edited"}

    cursor.execute("""
        UPDATE CHATBOT_FAQ_QUESTIONS
        SET QUESTION = ?
        WHERE AID = ?
    """, (new_question, aid))

    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "updated", "aid": aid}


def list_active_questions():
    conn = get_hana_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AID, QUESTION, CREATED_AT, CREATED_BY
        FROM CHATBOT_FAQ_QUESTIONS
        WHERE STATUS = 'ACTIVE'
        ORDER BY CREATED_AT DESC
    """)

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        {
            "aid": r[0],
            "question": r[1],
            "created_at": r[2].isoformat(),
            "created_by": r[3]
        }
        for r in rows
    ]


def list_deleted_questions():
    conn = get_hana_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AID, QUESTION, CREATED_AT, CREATED_BY
        FROM CHATBOT_FAQ_QUESTIONS
        WHERE STATUS = 'DELETED'
        ORDER BY CREATED_AT DESC
    """)

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        {
            "aid": r[0],
            "question": r[1],
            "created_at": r[2].isoformat(),
            "created_by": r[3]
        }
        for r in rows
    ]


###ROUTES DE CRUD
@app.route("/faq/restore", methods=["POST"])
def restore_question():
    data = request.json
    aid = data["aid"]

    conn = get_hana_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE CHATBOT_FAQ_QUESTIONS
        SET STATUS = 'PENDING'
        WHERE AID = ?
    """, (aid,))

    conn.commit()
    cursor.close()
    conn.close()

    return {"success": True}


@app.route("/faq/question", methods=["POST"])
def create_question():
    data = request.json
    if "question" not in data:
        abort(400, "Missing question")
    return create_pending_question(
        question=data["question"],
        created_by=get_user_role()
    )
    

@app.route("/faq/pending", methods=["GET"])
def get_pending():
    require_admin()
    return jsonify(list_pending_questions())

@app.route("/faq/answer", methods=["POST"])
def answer():
    require_admin()
    data = request.json
    return answer_question(data["aid"], data["answer"])


@app.route("/faq/delete", methods=["POST"])
def delete():
    require_admin()
    data = request.json
    return delete_question(data["aid"])

@app.route("/faq/update", methods=["POST"])
def update():
    require_admin()
    data = request.json
    return update_question(data["aid"], data["question"])

@app.route("/faq/active", methods=["GET"])
def get_active():
    require_admin()
    return jsonify(list_active_questions())

@app.route("/faq/deleted", methods=["GET"])
def get_deleted():
    require_admin()
    return jsonify(list_deleted_questions())

def translate_to_english(text: str) -> str:
    """
    Uses the same LLM to translate Spanish questions to English
    for vector search normalization.
    """
    prompt = f"""
Translate the following question to English.
Return ONLY the translated text, nothing else.

Question:
{text}
"""
    response = llm.invoke(prompt)
    return response.content.strip()

###FUNCION DE JOULE
@app.route("/joule/faq", methods=["POST"])
def joule_faq():
    data = request.get_json(silent=True) or {}
    question = data.get("question")

    if not question:
        return jsonify({
            "answer": "Pregunta vacía.",
            "confidence": 0.0
        }), 400

    result = faq_lookup(question)

    if result.get("found"):
        return jsonify({
            "answer": result["answer"],
            "confidence": 0.9
        })

    # Si no existe → registrar como pending (opcional)
    create_pending_question(question, created_by="JOULE")

    return jsonify({
        "answer": (
            "No encontré esta información en la base de conocimientos. "
            "He registrado la pregunta para que un administrador la revise."
        ),
        "confidence": 0.4
    })

# ==========================================
# NUEVAS RUTAS PARA SAP JOULE STUDIO (CORREGIDO)
# ==========================================

# 1. Herramienta de Búsqueda (Search Tool)
@app.route("/api/search", methods=["POST"])
def api_search():
    # Aseguramos que data sea un diccionario, incluso si llega vacío
    data = request.get_json(silent=True) or {}
    
    # CORRECCIÓN 1: Usar comillas para obtener el valor del JSON
    question = data.get("question") 

    if not question:
        return jsonify({"found": False, "error": "No question provided"}), 400

    # CORRECCIÓN 2: Pasar la variable 'question', no el string "question"
    result = faq_lookup(question)

    if result.get("found"):
        return jsonify({
            "found": True,
            "answer": result["answer"]
        })

    return jsonify({
        "found": False,
        "answer": None # Es bueno devolver explícitamente null o estructura vacía
    })

# 2. Herramienta de Registro (Register Tool)
@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json(silent=True) or {}
    question = data.get("question")

    if not question:
        return jsonify({"error": "Missing question"}), 400

    # Aquí llamamos a tu función existente que ya envía el correo
    create_pending_question(question, created_by="JOULE_USER")

    return jsonify({
        "success": True,
        "message": "La pregunta ha sido registrada exitosamente y el administrador ha sido notificado."
    })


# 3. Herramienta de Facturas (Opcional, si quieres mantenerla)
@app.route("/api/invoice", methods=["POST"])
def api_invoice():
    data = request.json
    invoice_id = data.get("invoice_id")
    # Llamamos a tu función existente
    status_text = get_invoice_status(invoice_id)
    return jsonify({"status_text": status_text})

# ==========================================



def needs_translation(text: str) -> bool:
    spanish_words = ["qué", "cómo", "cuál", "cuáles", "para", "es", "son"]
    t = text.lower()
    return (
        any(c in t for c in ["¿", "á", "é", "í", "ó", "ú", "ñ"])
        or any(w in t.split() for w in spanish_words)
    )


def faq_lookup(question: str):
    """
    Searches the internal SAP FAQ knowledge base using HANA vector similarity.
    Returns the stored answer if found, otherwise FAQ_NOT_FOUND.
    """
    conn = get_hana_connection()
    cursor = conn.cursor()

     # 1️⃣ Normalizar idioma
    if needs_translation(question):
        search_question = translate_to_english(question)
    else:
        search_question = question

    question_sql = search_question.replace("'", "''")

    sql = f"""
        SELECT AID, QUESTION, SCORE
        FROM (
            SELECT
                AID,
                QUESTION,
                COSINE_SIMILARITY(
                    QUESTION_VECTOR,
                    VECTOR_EMBEDDING(
                        '{question_sql}',
                        'QUERY',
                        'SAP_NEB.20240715'
                    )
                ) AS SCORE
            FROM CHATBOT_FAQ_QUESTIONS
            WHERE STATUS = 'ACTIVE'
              AND QUESTION_VECTOR IS NOT NULL
        )
        ORDER BY SCORE DESC
        LIMIT 1
    """

    cursor.execute(sql)
    row = cursor.fetchone()

    cursor.close()
    conn.close()

    if not row:
        return {"found": False}

    aid, question_db, score = row

    if score < SIMILARITY_THRESHOLD:
        return {"found": False}

    print(f"[FAQ] Best match AID={aid} SCORE={score}")


    conn = get_hana_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ANSWER FROM CHATBOT_FAQ_ANSWERS WHERE AID = ?",
        (aid,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row or not row[0]:
        return {"found": False}

    return {
        "found": True,
        "answer": row[0]
    }


def register_pending_faq(question: str) -> str:
    """
    Registers a new FAQ question as PENDING.
    """
    return create_pending_question(question, created_by="USER")

### Function for the AI agent to get an invoice status
def get_invoice_status(invoice_id: str) -> str:
   """Returns an invoice's status, ie whether it has been paid or not.

   Args:
      invoice_id: The invoide id
   """

   # This function mocks retrieving the invoice status from a live system
   # See SAP's API documentation for the real / live API that can provide this information from your system, ie
   # https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/19d48293097f4a2589433856b034dfa5/cb3caf09bd6749c59f0765981032b74e.html?locale=en-US
   options = ['Paid', 'Overdue', 'Unpaid, not due yet']
   invoice_status = random.choice(options)
   response = f"The status of invoice {invoice_id} is: {invoice_status}." 
    
   return response 

### Function for the AI agent to get an email address
def get_email_address(name: str) -> str:
   """Returns the person's email address

   Args:
      name: The name of the person whose email address is returned
   """

   dict = {}
   dict['Carlo'] = 'CarloJesus.GarciaTina.st@emeal.nttdata.com'
   dict['Ronald'] = 'RonaldYamil.CuellarButron.st@emeal.nttdata.com'
   dict['John'] = 'JohnGonzalo.SanabriaServa.st@emeal.nttdata.com'
   dict['Enrique'] = 'EnriqueBryan.CastilloTito.st@emeal.nttdata.com'
   dict['Admin'] = 'EnriqueBryan.CastilloTito.st@emeal.nttdata.com'
   dict['Administrador'] = 'EnriqueBryan.CastilloTito.st@emeal.nttdata.com'

   if name in dict.keys():
      response = dict[name]
   else:
      response = dict['Enrique']
   return response  

### Function for the AI agent to send an email

def send_email(recipient_name: str, email_address: str, email_text: str) -> str:
    """Send an email to a recipient using SMTP and return a status message."""
    # Usa las mismas variables que ya definiste arriba (temporalmente hardcodeadas)
    smtp_server = "sandbox.smtp.mailtrap.io"
    smtp_port = 587                 # STARTTLS (NO usar SMTP_SSL con 587)
    smtp_user = os.getenv("MAILTRAP_SMTP_USER")
    smtp_password = os.getenv("MAILTRAP_SMTP_PASS")  # Solo pruebas: NO dejar en claro en prod

    # Contenido del correo (incluye saludo y despedida, opcional)
    content = f"Hola {recipient_name},\n\n{email_text}\n\nSaludos,\nSAP BTP AI Agent"
    msg = MIMEText(content, "plain", "utf-8")
    msg["Subject"] = "Email from your SAP BTP AI Agent"
    msg["From"] = "SAP BTP AI Agent <ai-agent@btpaiagent.test>"
    msg["To"] = email_address

    # Conexión con STARTTLS en 587
    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.ehlo()
        server.starttls(context=context)     # << IMPORTANTE: elevar a TLS
        server.ehlo()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, [email_address], msg.as_string())

    return f"I sent the email to {recipient_name} ({email_address}):\n{email_text}"


### Function for the AI agent to get the text from a website
def get_text_from_link(url: str) -> str:
    """Fetches and extracts readable text from a public webpage URL."""

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator=" ")
        text = " ".join(text.split())

        return text[:8000]

    except Exception as e:
        return f"Error fetching content from URL: {str(e)}"

### Function for the AI agent to get the name of the current program on TV station ARTE
def get_live_tv_arte() -> str:
   """Return the currently playing program on the ARTE TV channel."""
   response = requests.get('https://api.arte.tv/api/player/v2/config/de/LIVE')
   data = response.json()
   title = data['data']['attributes']['metadata']['title']
   description = data['data']['attributes']['metadata']['description']

   return title + ': ' + description

#############################
# Setup the AI agent

tools = [faq_lookup, register_pending_faq, get_invoice_status, get_email_address, send_email, get_text_from_link, get_live_tv_arte]
llm = init_llm('anthropic--claude-3.5-sonnet', max_tokens=300)
llm_with_tools = llm.bind_tools(tools)
sys_msg = SystemMessage(content="""
You are an AI assistant named 'SAP BTP AI Agent'.

CRITICAL RULES (STRICT):
1. For ANY question related to SAP, internal processes, or FAQs, you MUST call the faq_lookup tool FIRST.
2. You are STRICTLY FORBIDDEN from answering from your own knowledge.
3. If faq_lookup returns found=true:
   - You MUST respond ONLY with the value of "answer".
   - You MUST NOT add explanations, summaries, or extra text.
4. If faq_lookup returns found=false:
   - Respond EXACTLY with: Esta pregunta no se encuentra registrada en la base de conocimientos.
   - Then ask: ¿Deseas registrar esta pregunta (Y/N)?
   - Do NOT call any tool until the user answers.
   
5. Depending on the Language entered, you must base your answer, for example: If the user asks you in Spanish, your answer must be in the same language.

Failure to follow these rules is an error.
""")
def assistant(state: AgentState):
    last_user_msg = state["messages"][-1].content

    # 1️⃣ Esperando confirmación
    if state.get("pending_question"):
        if is_affirmative(last_user_msg):
            create_pending_question(state["pending_question"], created_by="USER")

            state["messages"].append(
                AIMessage(content="Tu pregunta ha sido registrada y está pendiente de revisión.")
            )
            state["pending_question"] = None
            return state

        if is_negative(last_user_msg):
            state["messages"].append(
                AIMessage(content="Entendido. No se registrará la pregunta.")
            )
            state["pending_question"] = None
            return state


        state["messages"].append(
            AIMessage(content="Por favor responde únicamente con Y / Yes / Sí o N / No.")
        )
        return state

    # 2️⃣ Flujo normal
    response = llm_with_tools.invoke(state["messages"])
    state["messages"].append(response)

    if (
    isinstance(response, AIMessage)
    and "Esta pregunta no se encuentra registrada" in response.content
    ):
        state["pending_question"] = state["last_user_question"]
    
    return state



builder = StateGraph(AgentState)
builder.add_node("assistant", assistant)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "assistant")
builder.add_conditional_edges(
   "assistant",
   # If the latest message (result) from assistant is a tool call -> tools_condition routes to Tools
   # If the latest message (result) from assistant is not a tool call -> tools_condition routes to END
   tools_condition,
)
builder.add_edge("tools", "assistant")
graph = builder.compile()


@app.route('/', methods=['POST'])
def processing():
   
    default_input = "Has the invoice 1905 been paid?"
    payload = request.get_json(silent=True) or {}  # evita 415 si el Content-Type no es JSON
    
    conversation_id = payload.get("conversation_id", "default")
    user_input = payload.get("user_input")
    
    if not user_input:
        return jsonify({"btpaiagent_response": "Entrada vacía"}), 400

    # Recuperar estado previo
    state = SESSION_STORE.get(conversation_id, {
        "messages": [sys_msg],
        "pending_question": None
    })

    state["messages"].append(HumanMessage(content=user_input))
    state["last_user_question"] = user_input

    # Ejecutar agente
    agent_outcome = graph.invoke(state)

    # Guardar estado actualizado
    SESSION_STORE[conversation_id] = agent_outcome

    response = agent_outcome["messages"][-1].content

    # The more detailed log of the agent's response
    messages_extract = []
    for msg in agent_outcome['messages']:
        msg_actor = type(msg).__name__
        msg_text = msg.content
        if msg_actor == 'AIMessage':
            if not msg_text:
                msg_text = "[tool call]"
        if msg_actor == 'ToolMessage':
            msg_actor = msg_actor + ' (' + msg.name + ')'
        messages_extract.append([msg_actor, msg_text])
    btpaiagent_response_log = (str(messages_extract).replace('],', '],\n'))

	# Return the response and the log
    return jsonify({'btpaiagent_response': response, 'btpaiagent_response_log': btpaiagent_response_log})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )