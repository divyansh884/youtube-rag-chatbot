import os
import glob
import json
import re
import subprocess
import urllib.request
import imageio_ffmpeg
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

# Audio & Downloader Imports
import yt_dlp
from groq import Groq

# Load .env from backend directory or project root
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv()

chat_histories = {}

def get_session_history(session_id: str):
    if session_id not in chat_histories:
        chat_histories[session_id] = ChatMessageHistory()
    return chat_histories[session_id]

def find_cookie_file():
    """Finds cookies.txt in common project locations if supplied by the user."""
    possible_paths = [
        "cookies.txt",
        os.path.join(os.path.dirname(__file__), "cookies.txt"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt"),
    ]
    for p in possible_paths:
        if os.path.isfile(p):
            return p
    return None

def fetch_transcript_with_ytdlp(video_url: str):
    """Fetches video subtitles or auto-captions via yt-dlp using mobile player clients.
    
    This bypasses YouTube's web-bot blocking (HTTP 429 / 'Sign in to confirm you're not a bot').
    Prioritizes original spoken caption tracks to avoid 429 errors from virtual translated tracks.
    """
    cookie_file = find_cookie_file()
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web']
            }
        },
    }
    if cookie_file:
        ydl_opts['cookiefile'] = cookie_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        subtitles = info.get('subtitles') or {}
        auto_subtitles = info.get('automatic_captions') or {}

        preferred_langs = ["en", "en-US", "en-GB", "es", "fr", "de", "it", "pt", "hi", "ja", "ko", "ru", "zh", "ar"]

        # Build prioritized list of candidate caption tracks
        candidate_tracks = []

        # 1. Manual/human-uploaded subtitles (highest reliability)
        for lang in preferred_langs:
            if lang in subtitles:
                candidate_tracks.append((lang, subtitles[lang]))
        if not candidate_tracks and subtitles:
            candidate_tracks.extend(subtitles.items())

        # 2. Separate automatic captions into original (no tlang=) and translated (with tlang=)
        original_auto = []
        translated_auto = []
        for lang, formats in auto_subtitles.items():
            if not formats:
                continue
            first_url = formats[0].get('url', '')
            if 'tlang=' not in first_url:
                original_auto.append((lang, formats))
            else:
                translated_auto.append((lang, formats))

        # Check for preferred language among original auto-captions
        for lang, formats in original_auto:
            if any(pref in lang for pref in ["en", "en-US", "en-GB"]):
                candidate_tracks.append((lang, formats))
        for item in original_auto:
            if item not in candidate_tracks:
                candidate_tracks.append(item)

        # 3. Translated auto-captions as fallback
        for lang in preferred_langs:
            for l, formats in translated_auto:
                if l == lang and (l, formats) not in candidate_tracks:
                    candidate_tracks.append((l, formats))

        if not candidate_tracks:
            return None

        # Attempt downloading from candidate tracks in order of priority
        for track_lang, target_tracks in candidate_tracks:
            try:
                selected = next((s for s in target_tracks if s.get('ext') == 'json3'), None)
                ext = 'json3'
                if not selected:
                    selected = next((s for s in target_tracks if s.get('ext') == 'vtt'), None)
                    ext = 'vtt'
                if not selected:
                    selected = target_tracks[0]
                    ext = selected.get('ext', '')

                req = urllib.request.Request(selected['url'], headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as resp:
                    content = resp.read().decode('utf-8')

                if ext == 'json3':
                    data = json.loads(content)
                    parts = []
                    for ev in data.get('events', []):
                        for seg in ev.get('segs', []):
                            u = seg.get('utf8', '')
                            if u and u != '\n':
                                parts.append(u)
                    transcript_text = ' '.join(''.join(parts).split())
                else:
                    lines = []
                    for line in content.splitlines():
                        line = line.strip()
                        if not line or line.startswith('WEBVTT') or '-->' in line or line.isdigit():
                            continue
                        clean_line = re.sub(r'<[^>]+>', '', line)
                        if clean_line:
                            lines.append(clean_line)
                    transcript_text = ' '.join(lines)

                if transcript_text and len(transcript_text) > 30:
                    print(f"Loaded captions in '{track_lang}' ({len(transcript_text)} characters).")
                    return [Document(page_content=transcript_text, metadata={"source": video_url})]
            except Exception as e:
                # If a translated track returned 429, try the next candidate
                continue

        return None

def transcribe_audio_with_groq(video_url: str):
    """Downloads the audio track and transcribes it using Groq's Whisper model.
    
    Automatically handles files > 25MB by compressing to 16kHz mono 32kbps MP3
    and splitting into 15-minute segments if needed.
    """
    print("Captions unavailable; falling back to Whisper audio transcription...")
    
    cookie_file = find_cookie_file()
    video_id = video_url.split("v=")[-1].split("&")[0]
    raw_download_tmpl = f"temp_audio_{video_id}.%(ext)s"
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    # 1. Download stream with mobile player clients
    ydl_opts = {
        'format': 'ba/b[ext=m4a]/bestaudio/best',
        'outtmpl': raw_download_tmpl,
        'quiet': True,
        'no_warnings': True,
        'rm_cachedir': True,
        'ffmpeg_location': ffmpeg_exe,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb']
            }
        },
    }
    if cookie_file:
        ydl_opts['cookiefile'] = cookie_file
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
            
        downloaded_files = glob.glob(f"temp_audio_{video_id}.*")
        if not downloaded_files:
            raise ValueError("Failed to download audio track.")
            
        raw_audio_path = downloaded_files[0]
        
        # 2. Compress audio to 16kHz mono 32kbps MP3 (ideal for Whisper & reduces size by 85%)
        compressed_path = f"comp_audio_{video_id}.mp3"
        compress_cmd = [
            ffmpeg_exe, '-y', '-i', raw_audio_path,
            '-vn', '-ac', '1', '-ar', '16000', '-b:a', '32k',
            compressed_path
        ]
        subprocess.run(compress_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        comp_size_mb = os.path.getsize(compressed_path) / (1024 * 1024)
        
        # 3. If audio is still > 24MB (e.g., > 1.5 hours long), split into 15-minute chunks
        chunk_files = []
        if comp_size_mb > 24.0:
            segment_pattern = f"chunk_audio_{video_id}_%03d.mp3"
            split_cmd = [
                ffmpeg_exe, '-y', '-i', compressed_path,
                '-f', 'segment', '-segment_time', '900', '-c', 'copy',
                segment_pattern
            ]
            subprocess.run(split_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            chunk_files = sorted(glob.glob(f"chunk_audio_{video_id}_*.mp3"))
        else:
            chunk_files = [compressed_path]

        # 4. Transcribe chunk(s) via Groq Whisper API
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        transcriptions = []
        for chunk in chunk_files:
            with open(chunk, "rb") as file:
                res = client.audio.transcriptions.create(
                    file=(os.path.basename(chunk), file.read()),
                    model="whisper-large-v3",
                    response_format="text"
                )
                if res:
                    transcriptions.append(res)
                    
        full_text = " ".join(transcriptions).strip()
        if not full_text:
            raise ValueError("Whisper transcription returned empty text.")

        return [Document(page_content=full_text, metadata={"source": video_url})]

    finally:
        # Clean up all raw, compressed, and chunk files
        for pattern in [f"temp_audio_{video_id}.*", f"comp_audio_{video_id}.*", f"chunk_audio_{video_id}_*"]:
            for f in glob.glob(pattern):
                try:
                    os.remove(f)
                except OSError:
                    pass

def index_youtube_video(video_url: str):
    print(f"Indexing video: {video_url}")
    docs = None
    
    # ATTEMPT 1: Try getting the fast text transcript first via YoutubeLoader
    try:
        loader = YoutubeLoader.from_youtube_url(
            video_url, 
            add_video_info=False,
            language=["en", "en-US", "es", "fr", "de", "it", "pt", "hi", "ja", "ko", "ru", "zh", "ar"],
            translation="en" 
        )
        loaded = loader.load()
        if loaded and loaded[0].page_content.strip():
            docs = loaded
            print("Loaded transcript via YoutubeLoader.")
    except Exception as e:
        print(f"YoutubeLoader unavailable ({type(e).__name__}). Trying yt-dlp captions...")

    # ATTEMPT 2: Fallback to yt-dlp captions (bypasses YouTube IP/bot restrictions)
    if not docs:
        try:
            docs = fetch_transcript_with_ytdlp(video_url)
            if docs:
                print("Loaded transcript via yt-dlp captions.")
        except Exception as e:
            print(f"yt-dlp caption extraction failed ({type(e).__name__}): {e}")

    # ATTEMPT 3: Fallback to Whisper audio transcription if video has no captions
    if not docs:
        docs = transcribe_audio_with_groq(video_url)

    if not docs:
        raise ValueError(f"Unable to extract transcript or audio for video: {video_url}")

    # Chunk the text
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    chunks = text_splitter.split_documents(docs)
    
    # Create Vector Store
    raw_id = video_url.split("v=")[-1].split("&")[0]
    clean_id = re.sub(r'[^a-zA-Z0-9._-]', '_', raw_id).strip('._-')
    collection_name = f"vid_{clean_id}" if clean_id else "vid_default"
    
    vectorstore = Chroma.from_documents(
        documents=chunks, 
        embedding=HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2"),
        collection_name=collection_name
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
        "You are an articulate, accurate YouTube video AI assistant.\n"
        "Use ONLY the following retrieved context from the video's transcript or audio to answer the user's question.\n"
        "If the user asks something that is not mentioned in the context, you MUST reply: "
        "'This topic was not discussed in the video.' Do not guess, assume, or use outside knowledge.\n\n"
        "Formatting Guidelines:\n"
        "- Format your answer cleanly using Markdown for high readability.\n"
        "- Use descriptive section headings (e.g. '### Overview', '### Key Points') when organizing longer explanations or summaries.\n"
        "- Use bullet points (`- **Keyword:** description`) with bold keywords for lists, placing each bullet on its own line.\n"
        "- Separate paragraphs and sections with clean spacing.\n"
        "- Highlight important names, numbers, rules, or timestamps with **bold** text.\n\n"
        "Context:\n{context}"
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
