from flask import Flask, render_template, request, jsonify, session
import uuid
from rag_pipeline import setup_rag_pipeline, process_query, session_memories

app = Flask(__name__)
app.secret_key = "your_secure_secret_key"  # For session management

# Initialize the RAG pipeline
setup_rag_pipeline()

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    query = data.get("query", "")
    if not query:
        return jsonify({"error": "Empty query"}), 400
    
    # Get or create session ID
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
    
    # Process query with session context
    answer, chunks = process_query(query, session['session_id'], return_chunks=True)
    return jsonify({"answer": answer, "chunks": chunks})

@app.route("/reset", methods=["POST"])
def reset_conversation():
    # Reset conversation history
    if 'session_id' in session:
        session_id = session['session_id']
        if session_id in session_memories:
            del session_memories[session_id]
    
    return jsonify({"status": "conversation reset"})

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True)