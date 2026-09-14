import uuid
import os
import boto3
from Backend.APII import ask_pdf, is_pdf_safe, process_and_store_pdf
from flask import Flask, redirect, url_for, render_template, request, session, jsonify, send_from_directory
from datetime import timedelta, datetime
from pypdf import PdfReader
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

app = Flask(__name__, 
            template_folder='frontend/templates', 
            static_folder='frontend/static')

# Temporary local folder used only during processing
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# AWS S3 Configuration
S3_BUCKET_NAME = "uploads-pdf-chat"
s3_client = boto3.client('s3')

app.secret_key="!@#$%^&*()"
app.permanent_session_lifetime= timedelta(minutes=5)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///chat_history.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Document(db.Model):
    id = db.Column(db.String(36), primary_key=True) 
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    doc_id = db.Column(db.String(36), db.ForeignKey('document.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False) 
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == 'POST':
        uploaded_file = request.files.get('wsw')
        
        if not uploaded_file or uploaded_file.filename == '':
            return "No file selected", 400
            
        if not uploaded_file.filename.lower().endswith('.pdf'):
            return "Please upload a PDF file", 400

        try:
            doc_id = str(uuid.uuid4())
            s3_filename = f"{doc_id}.pdf"
            
            # 1. Upload file directly to S3 bucket
            s3_client.upload_fileobj(uploaded_file, S3_BUCKET_NAME, s3_filename)
            
            # 2. Download temporarily to local server to extract text & validate
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], s3_filename)
            s3_client.download_file(S3_BUCKET_NAME, s3_filename, file_path)

            pdf_reader = PdfReader(file_path)
            extracted_text = "".join([page.extract_text() + "\n" for page in pdf_reader.pages if page.extract_text()])
            
            if not extracted_text.strip():
                os.remove(file_path)
                s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_filename)
                return "Uploaded PDF contains no readable text.", 400

            if not is_pdf_safe(extracted_text):
                os.remove(file_path)
                s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=s3_filename)
                return "Upload rejected: This document does not appear to be a valid, safe document.", 403
            
            process_and_store_pdf(extracted_text, doc_id)
            
            # Clean up local temp file (leaving it safely stored in S3)
            if os.path.exists(file_path):
                os.remove(file_path)

            new_doc = Document(id=doc_id, filename=uploaded_file.filename)
            db.session.add(new_doc)
            db.session.commit()

            session["doc_id"] = doc_id
            return redirect(url_for('output'))
        except Exception as e:
            return f"Error reading PDF: {str(e)}", 500
    
    return render_template("Home/index.html")

@app.route("/output", methods=["GET"])
def output():
    if not session.get("doc_id"):
        return redirect(url_for("home"))
    return render_template("Output/index.html")

@app.route("/api/history", methods=["GET"])
def get_history():
    docs = Document.query.order_by(Document.created_at.desc()).all()
    history_list = [{"id": doc.id, "filename": doc.filename, "date": doc.created_at.strftime("%b %d, %Y")} for doc in docs]
    return jsonify(history_list)

@app.route("/api/load_chat/<doc_id>", methods=["GET"])
def load_chat(doc_id):
    doc = Document.query.get(doc_id)
    if not doc:
        return jsonify({"error": "Document not found"}), 404
    
    session["doc_id"] = doc_id 
    
    messages = Message.query.filter_by(doc_id=doc_id).order_by(Message.timestamp.asc()).all()
    chat_data = [{"sender": "user" if msg.role == "User" else "ai", "text": msg.content} for msg in messages]
    
    return jsonify({"filename": doc.filename, "chat": chat_data})

@app.route("/api/load_chat/active", methods=["GET"])
def load_active_chat():
    doc_id = session.get("doc_id")
    if not doc_id:
        return jsonify({"chat": []})
    
    messages = Message.query.filter_by(doc_id=doc_id).order_by(Message.timestamp.asc()).all()
    chat_data = [{"sender": "user" if msg.role == "User" else "ai", "text": msg.content} for msg in messages]
    return jsonify({"chat": chat_data})

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("message")
    doc_id = session.get("doc_id")

    if not doc_id:
        return jsonify({"error": "Session expired or no PDF found. Please re-upload."}), 400

    if not user_input:
        return jsonify({"error": "Message is empty"}), 400
    try:
        all_messages = Message.query.filter_by(doc_id=doc_id).order_by(Message.timestamp.asc()).all()
        
        if len(all_messages) > 8:
            older_msgs = all_messages[:-8]
            recent_msgs = all_messages[-8:]
        else:
            older_msgs = []
            recent_msgs = all_messages
            
        older_text = "\n".join([f"{msg.role}: {msg.content}" for msg in older_msgs])
        recent_text = "\n".join([f"{msg.role}: {msg.content}" for msg in recent_msgs])

        user_msg = Message(doc_id=doc_id, role="User", content=user_input)
        db.session.add(user_msg)
        db.session.commit()

        answer = ask_pdf(doc_id, user_input, older_text, recent_text)

        ai_msg = Message(doc_id=doc_id, role="AI", content=answer)
        db.session.add(ai_msg)
        db.session.commit()

        return jsonify({"response": answer})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/delete_chat/<doc_id>", methods=["DELETE"])
def delete_chat(doc_id):
    try:
        Message.query.filter_by(doc_id=doc_id).delete()
        
        doc = Document.query.get(doc_id)
        if doc:
            db.session.delete(doc)
            
        db.session.commit()
        
        # Remove PDF from S3 bucket
        s3_client.delete_object(Bucket=S3_BUCKET_NAME, Key=f"{doc_id}.pdf")
        
        # Also clean up local file if it exists
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{doc_id}.pdf")
        if os.path.exists(file_path):
            os.remove(file_path)
        
        if session.get("doc_id") == doc_id:
            session.pop("doc_id", None)
            
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/pdf/active", methods=["GET"])
def serve_active_pdf():
    doc_id = session.get("doc_id")
    if not doc_id:
        return "<h3>No active document</h3>", 404
        
    filename = f"{doc_id}.pdf"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # If not cached locally, pull it down from S3 on demand
    if not os.path.exists(file_path):
        try:
            s3_client.download_file(S3_BUCKET_NAME, filename, file_path)
        except Exception:
            return """
            <div style="font-family: sans-serif; text-align: center; margin-top: 50px; color: #6c757d;">
                <h3>PDF Not Available</h3>
                <p>The original PDF file could not be retrieved from S3 storage.</p>
            </div>
            """, 404
        
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)