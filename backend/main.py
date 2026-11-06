from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from rag import index_youtube_video, create_chat_chain

app = FastAPI(title="YouTube RAG API")

# VERY IMPORTANT: Allow Chrome Extension to communicate with this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to your extension ID
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data model for incoming requests
class ChatRequest(BaseModel):
    url: str
    message: str

# In-memory dictionary to store active video chains so we don't re-download 
# the transcript every time the user sends a new message.
active_sessions = {}

@app.post("/chat")
async def chat_with_video(request: ChatRequest):
    try:
        # Clean the URL to use as a consistent session ID (removes timestamp parameters like &t=4s)
        session_id = request.url.split("&")[0]
        
        # If this is the first time asking about this URL, process the video
        if session_id not in active_sessions:
            retriever = index_youtube_video(session_id)
            active_sessions[session_id] = create_chat_chain(retriever)
        
        # Get the chain for this specific video
        chain = active_sessions[session_id]
        
        # Ask the LLM the question, passing the session_id to inject previous chat history
        response = chain.invoke(
            {"input": request.message},
            config={"configurable": {"session_id": session_id}}
        )
        
        return {"answer": response["answer"]}
        
    except Exception as e:
        print(f"Error: {str(e)}") # Prints error to your terminal for debugging
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
def health_check():
    return {"status": "Backend is running!"}