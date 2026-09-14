import os
import nest_asyncio
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from nemoguardrails import LLMRails, RailsConfig
from langchain_text_splitters import RecursiveCharacterTextSplitter

from langchain_qdrant import QdrantVectorStore, FastEmbedSparse, RetrievalMode
from langchain_community.embeddings.fastembed import FastEmbedEmbeddings
from qdrant_client import QdrantClient
from qdrant_client.http import models

from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_community.document_compressors.flashrank_rerank import FlashrankRerank

from Backend.prompt import qa_prompt, intent_check_prompt, doc_check_prompt, summary_prompt 

nest_asyncio.apply()
load_dotenv()

primary_llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.1,
    api_key=os.getenv("GROQ_API_KEY"),
    max_retries=2,
)

fallback_llm = ChatOpenAI(
    model="nex-agi/nex-n2.5-mini:free", 
    temperature=0.1,
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
    max_retries=2
)

llm = primary_llm.with_fallbacks([fallback_llm])

config = RailsConfig.from_path("./Backend/config")
guardrails = LLMRails(config)

embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
sparse_embeddings = FastEmbedSparse(model_name="Qdrant/bm25")

collection_name = "pdf_documents"
qdrant_client = QdrantClient(path="./qdrant_db")

if not qdrant_client.collection_exists(collection_name):
    qdrant_client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=384, 
            distance=models.Distance.COSINE
        ),
        sparse_vectors_config={
            "langchain-sparse": models.SparseVectorParams()
        }
    )

vector_store = QdrantVectorStore(
    client=qdrant_client,
    collection_name=collection_name,
    embedding=embeddings,
    sparse_embedding=sparse_embeddings,
    retrieval_mode=RetrievalMode.HYBRID,
)

compressor = FlashrankRerank(top_n=4)

def process_and_store_pdf(full_text: str, doc_id: str):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, 
        chunk_overlap=200
    )
    
    chunks = text_splitter.create_documents(
        [full_text], 
        metadatas=[{"doc_id": doc_id}]
    )
    
    vector_store.add_documents(chunks)

dock_chain = doc_check_prompt | llm | StrOutputParser()

def is_pdf_safe(full_text: str) -> bool:
    snippet = full_text[:2000]
    result = dock_chain.invoke({"doc_snippet": snippet}).strip().upper()
    return result == "SAFE"

qa_chain = qa_prompt | llm | StrOutputParser()
intent_chain = intent_check_prompt | llm | StrOutputParser()

def ask_pdf(doc_id: str, question: str, older_text: str, recent_text: str) -> str:
    guard_res = guardrails.generate(messages=[{"role": "user", "content": question}])
    if "I am a factual document analysis tool" in guard_res["content"] or "I am a specialized document assistant" in guard_res["content"]:
        return guard_res["content"]

    is_smuggling = intent_chain.invoke({"question": question}).strip().upper()
    if "YES" in is_smuggling:
        return "Request rejected: The prompt contains forbidden framing (analogies, personas, or formatting overrides)."

    chat_context = ""
    
    if older_text.strip():
        summary_chain = summary_prompt | llm | StrOutputParser()
        summary = summary_chain.invoke({"chat_history": older_text})
        chat_context += f"[Summary of earlier conversation]:\n{summary}\n\n"
        
    if recent_text.strip():
        chat_context += f"[Recent conversation]:\n{recent_text}"
        
    if not chat_context:
        chat_context = "No previous conversation."

    base_retriever = vector_store.as_retriever(
        search_kwargs={
            "k": 15, 
            "filter": models.Filter(
                must=[models.FieldCondition(
                    key="metadata.doc_id", 
                    match=models.MatchValue(value=doc_id)
                )]
            )
        }
    )

    compression_retriever = ContextualCompressionRetriever(
        base_compressor=compressor,
        base_retriever=base_retriever
    )
    
    relevant_docs = compression_retriever.invoke(question)
    context = "\n\n".join([doc.page_content for doc in relevant_docs])
    
    if not context:
        return "I could not find any information relevant to that question in the document."

    answer = qa_chain.invoke({
        "chat_history": chat_context, 
        "context": context, 
        "question": question
    })
    
    return answer
#textspliiter
#embed
#vector
