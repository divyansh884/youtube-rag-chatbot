import os
import glob
from dotenv import load_dotenv

# LangChain Imports
from langchain_community.document_loaders import YoutubeLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_classic.chains import create_retrieval_chain, create_history_aware_retriever
from langchain_classic.chains.combine_documents import create_stuff_documents_chain
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.documents import Document
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings

# New Audio Imports
import yt_dlp
from groq import Groq

load_dotenv()
chat_histories = {}

def get_session_history(session_id: str):
    if session_id not in chat_histories:
        chat_histories[session_id] = ChatMessageHistory()
    return chat_histories[session_id]

def transcribe_audio_with_groq(video_url: str):
    """Downloads the audio track and transcribes it using Groq's Whisper model."""
    print("No captions found! Falling back to Whisper audio transcription...")
    
    # 1. Download the audio stream (m4a format avoids needing ffmpeg installed)
    ydl_opts = {
        'format': 'm4a/bestaudio/best',
        'outtmpl': 'temp_audio.%(ext)s',
        'quiet': True,
        'rm_cachedir': True, # Clears the broken signature cache
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])
        
    # Find the downloaded file
    downloaded_files = glob.glob("temp_audio.*")
    if not downloaded_files:
        raise ValueError("Failed to download audio track.")
        
    audio_file_path = downloaded_files[0]
    
    # 2. Transcribe the audio file with Groq
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    with open(audio_file_path, "rb") as file:
        transcription = client.audio.transcriptions.create(
          file=(audio_file_path, file.read()),
          model="whisper-large-v3",
          response_format="text"
        )
        
    # 3. Clean up the local file so we don't waste hard drive space
    os.remove(audio_file_path)
    
    # 4. Wrap the text in a LangChain Document so the vector DB can read it
    return [Document(page_content=transcription, metadata={"source": video_url})]


def index_youtube_video(video_url: str):
    print(f"Indexing video: {video_url}")
    
    # ATTEMPT 1: Try getting the fast, free text transcript first
    try:
        loader = YoutubeLoader.from_youtube_url(
            video_url, 
            add_video_info=False,
            language=["en", "en-US", "es", "fr", "de", "it", "pt", "hi", "ja", "ko", "ru", "zh", "ar"],
            translation="en" 
        )
        docs = loader.load()
        if not docs:
            raise ValueError("Transcript empty.")
            
    except Exception as e:
        # ATTEMPT 2: Fallback to AI audio transcription
        docs = transcribe_audio_with_groq(video_url)

    # Chunk the text
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_documents(docs)
    
    # Create Vector Store
    video_id = video_url.split("v=")[-1].split("&")[0]
    
    vectorstore = Chroma.from_documents(
        documents=chunks, 
        embedding=HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2"),
        collection_name=video_id
    )
    
    return vectorstore.as_retriever(search_kwargs={"k": 4})


def create_chat_chain(retriever):
    # Using the Qwen model your API key has access to
    llm = ChatGroq(model="qwen/qwen3.8-27b", temperature=0)
    
    # History-Aware Retriever
    contextualize_q_system_prompt = (
        "Given a chat history and the latest user question "
        "which might reference context in the chat history, "
        "formulate a standalone question which can be understood "
        "without the chat history. Do NOT answer the question, "
        "just reformulate it if needed and otherwise return it as is."
    )
    contextualize_q_prompt = ChatPromptTemplate.from_messages([
        ("system", contextualize_q_system_prompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])
    
    history_aware_retriever = create_history_aware_retriever(
        llm, retriever, contextualize_q_prompt
    )
    
    # Answer Generation
    system_prompt = (
        "You are a strict YouTube video assistant. "
        "Use ONLY the following retrieved context from the video's transcript or audio to answer the user's question. "
        "If the user asks something that is not mentioned in the context, you MUST reply: "
        "'This topic was not discussed in the video.' Do not guess, assume, or use outside knowledge.\n\n"
        "Context: {context}"
    )
    qa_prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder("chat_history"),
        ("human", "{input}"),
    ])
    
    question_answer_chain = create_stuff_documents_chain(llm, qa_prompt)
    rag_chain = create_retrieval_chain(history_aware_retriever, question_answer_chain)
    
    conversational_rag_chain = RunnableWithMessageHistory(
        rag_chain,
        get_session_history,
        input_messages_key="input",
        history_messages_key="chat_history",
        output_messages_key="answer",
    )
    
    return conversational_rag_chain