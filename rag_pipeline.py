# rag_pipeline.py

import os
import fitz
import shutil
import threading
from urllib.parse import quote
from http.server import SimpleHTTPRequestHandler
from socketserver import TCPServer
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
# Add to rag_pipeline.py
from langchain.memory import ConversationBufferMemory

# Initialize memory for each session
def create_memory():
    return ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True,
        output_key="answer"
    )

# Session storage (for Flask implementation)
session_memories = {}


# === Configuration ===
DATA_PATH = os.path.join(os.getcwd(), "data", "pdf_files")
CHROMA_PATH = "chroma_db"
PDF_SERVER_PORT = 8000
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=500)
embedding_function = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2", model_kwargs={'device': 'cpu'})
db = None

# === Step 1: Start PDF File Server ===
def start_pdf_server():
    os.chdir(DATA_PATH)

    class PDFHandler(SimpleHTTPRequestHandler):
        def translate_path(self, path):
            path = path.split("?", 1)[0].split("#", 1)[0]
            return os.path.join(DATA_PATH, path.lstrip("/"))

    def run_server():
        with TCPServer(("", PDF_SERVER_PORT), PDFHandler) as httpd:
            print(f"📄 PDF Server running at http://localhost:{PDF_SERVER_PORT}/")
            httpd.serve_forever()

    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()

# === Step 2: PDF Text Extraction ===
def extract_pdf_text(file_path):
    doc = fitz.open(file_path)
    chunks = []
    for page_num in range(len(doc)):
        text = doc[page_num].get_text("text").strip()
        if text:
            chunks.append({"page": page_num + 1, "text": text})
    return chunks

# === Step 3: Load PDF Documents ===
def load_pdf_documents():
    docs = []
    for filename in os.listdir(DATA_PATH):
        if filename.endswith(".pdf"):
            path = os.path.join(DATA_PATH, filename)
            extracted = extract_pdf_text(path)
            for chunk in extracted:
                docs.append({
                    "source": filename,
                    "page_number": chunk["page"],
                    "content": chunk["text"]
                })
    return docs

# === Step 4: Set up RAG pipeline ===
def setup_rag_pipeline():
    global db
    db = Chroma(
        collection_name="pdf_collection",
        embedding_function=embedding_function,
        persist_directory=CHROMA_PATH
    )

    if db._collection.count() > 0:
        print(f"✅ ChromaDB already has {db._collection.count()} documents.")
        return

    print("🔁 Creating Chunks & Populating ChromaDB...")

    shutil.rmtree(CHROMA_PATH, ignore_errors=True)
    os.makedirs(CHROMA_PATH, exist_ok=True)

    documents = load_pdf_documents()
    chunk_docs = []
    for doc in documents:
        split = text_splitter.create_documents([doc["content"]])
        for i, chunk in enumerate(split):
            chunk.metadata = {
                "source": doc["source"],
                "page_number": doc["page_number"],
                "chunk_id": i + 1
            }
            chunk_docs.append(chunk)

    db = Chroma(
        collection_name="pdf_collection",
        embedding_function=embedding_function,
        persist_directory=CHROMA_PATH
    )
    db.add_documents(chunk_docs)
    print("✅ Chunks created and stored!")

# === Step 5: Process Query ===
# Modified process_query function
def process_query(query_text, session_id, return_chunks=False):
    global db, session_memories
    
    # Get or create session memory
    if session_id not in session_memories:
        session_memories[session_id] = create_memory()
    memory = session_memories[session_id]
    
    # Get conversation history
    chat_history = memory.chat_memory.messages
    
    # Retrieve relevant chunks
    results = db.similarity_search(query_text, k=5)
    
    # Process chunks 
    top_chunks = []
    context_parts = []
    for doc in results:
        source = doc.metadata["source"]
        page_number = doc.metadata.get("page_number", 1)
        link = f"http://localhost:{PDF_SERVER_PORT}/{quote(source)}#page={page_number}"
        chunk = {
            "page": page_number,
            "source": source,
            "text": doc.page_content,
            "link": link
        }
        top_chunks.append(chunk)
        context_parts.append(doc.page_content)
    
    context = "\n\n---\n\n".join(context_parts)
    
    # Create a new prompt that includes conversation history
    template = ChatPromptTemplate.from_template("""
    Answer the question based on the following context and chat history:
    
    CONTEXT:
    {context}
    
    CHAT HISTORY:
    {chat_history}
    
    CURRENT QUESTION: {question}
    
    Answer the current question based on both the context and the conversation history.
    If you need to refer to previous questions and answers, do so.
    """)
    
    # Format history into a readable string
    formatted_history = ""
    for msg in chat_history:
        if hasattr(msg, 'type') and msg.type == 'human':
            formatted_history += f"Human: {msg.content}\n"
        elif hasattr(msg, 'type') and msg.type == 'ai':
            formatted_history += f"AI: {msg.content}\n"
    
    prompt = template.format(
        context=context, 
        chat_history=formatted_history, 
        question=query_text
    )
    
    model = OllamaLLM(model="deepseek-r1")
    response = model.invoke(prompt)
    
    # Save the interaction to memory
    memory.save_context({"input": query_text}, {"answer": response})
    
    if return_chunks:
        return response, top_chunks
    return response

# === Step 6: Bootstrapping Everything ===
start_pdf_server()
setup_rag_pipeline()
