import os, json, requests, random, smtplib, ssl
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from datetime import datetime
from gen_ai_hub.proxy.langchain.init_models import init_llm
from langchain_core.messages import SystemMessage
from langgraph.graph import START, StateGraph, MessagesState
from langgraph.prebuilt import tools_condition, ToolNode
from flask import Flask, request, jsonify
import ssl


app = Flask(__name__)
# Port number is required to fetch from env variable
# http://docs.cloudfoundry.org/devguide/deploy-apps/environment-variable.html#PORT
cf_port = os.getenv("PORT")

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

#############################
# Provide the tools / functions for the AI agent

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
   dict['Carlo'] = 'carlo@tuempresa.com'
   dict['Ronald'] = 'ronald@tuempresa.com'
   dict['John'] = 'john@tuempresa.com'
   dict['Enrique'] = 'Enrique@tuempresa.com'

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


### Function for the AI agent to answer questions about SAP
def answer_SAP_question(text: str) -> str:
   """Responds to questions about the company SAP

   Args:
      text: The question about SAP
   """

   backend_api = "LA URL DE TU AGENTE FAQ EN CLOUD FOUNDRY"
   user_input = text
   paylod = {'user_request': user_input}
   headers = {'Accept' : 'application/json', 'Content-Type' : 'application/json'}
   r = requests.get(backend_api, json=paylod, headers=headers, verify=False)
   response = r.json()
   faq_response = response['faq_response']
   faq_response_log = response['faq_response_log']
    
   return faq_response

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

### Function for the AI agent to get the lunch menu
def get_lunch_menu() -> str:
   """Returns today's menu of the SAP canteen in Zurich
   """
   # Scrape the canteen's website
   response = requests.get("https://circle.sv-restaurant.ch/de/menuplan/chreis-14/")
   soup = BeautifulSoup(response.content, 'html.parser')
    
   # Get current day of week
   dt = datetime.now()
   weekday_current = dt.weekday()
    
   # If called on weekend, use Monday instead
   if weekday_current < 5:
       weekday_menu = weekday_current
   else:
       weekday_menu = 0
    
   # Get date for which menu will be returned
   dates_raw = soup.find_all(class_='date')
   dates = []
   for day in dates_raw:
       dates.append(day.text)
   date = dates[0] # Past dates are removed from the restaurant page
    
   # Get menus for that date
   menus = []
   menus_raw = dates_raw = soup.find_all(id='menu-plan-tab' + str(weekday_menu))
   menus_all_raw = soup.find(id='menu-plan-tab1')
   menus_all = menus_all_raw.find_all(class_='menu-title')
   for menu in menus_all:
       if menu.text not in ['Lunch auf der Terrasse']:
           menus.append(menu.text)
           
   # Prepare the response with the above information
   menu_flowtext = ''
   for i in range(len(menus)):
       menu_flowtext += " " + str(i+1) + ") " + menus[i]
   menu_flowtext = menu_flowtext.lstrip()    
   response = f"On {weekday_menu}, the {date}, Chreis 14 serves {menu_flowtext}."

   return response

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

tools = [get_invoice_status, get_email_address, send_email, answer_SAP_question, get_text_from_link, get_lunch_menu, get_live_tv_arte]
llm = init_llm('anthropic--claude-3.5-sonnet', max_tokens=300)
llm_with_tools = llm.bind_tools(tools)
sys_msg = SystemMessage(content="You are a helfpul assistant tasked with answering questions about different topics. Your name is 'SAP BTP AI Agent'. Keep your answers short. After giving a response, do not ask for additional requests. Instead of referring to a link on your response call the function get_text_from_link to get the information from a given link yourself. Only use information that is provided to you by the different tools you are given.")

def assistant(state: MessagesState):
   return {"messages": [llm_with_tools.invoke([sys_msg] + state["messages"])]}

builder = StateGraph(MessagesState)
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
    
    
    user_input = payload.get('user_input', default_input)


    # Pass the request to the AI agent
    agent_outcome = graph.invoke({"messages": [("user", user_input)]})

    # The agent's response
    btpaiagent_response = agent_outcome['messages'][-1].to_json()['kwargs']['content']

    # The more detailed log of the agent's response
    messages_extract = []
    for msg in agent_outcome['messages']:
        msg_actor = type(msg).__name__
        msg_text = msg.content
        if msg_actor == 'AIMessage':
            if msg_text == '':
               msg_text = msg.tool_calls
        if msg_actor == 'ToolMessage':
            msg_actor = msg_actor + ' (' + msg.name + ')'
        messages_extract.append([msg_actor, msg_text])
    btpaiagent_response_log = (str(messages_extract).replace('],', '],\n'))

	# Return the response and the log
    return jsonify({'btpaiagent_response': btpaiagent_response, 'btpaiagent_response_log': btpaiagent_response_log})

if __name__ == '__main__':
	if cf_port is None:
		app.run(host='0.0.0.0', port=5000, debug=True)
	else:
		app.run(host='0.0.0.0', port=int(cf_port), debug=True)