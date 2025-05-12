#!/usr/bin/env python3
"""
Professional RAG Script supporting OpenAI, Google Gemini, and DeepSeek via config.
"""
import asyncio
import os
import sys
import yaml
from dotenv import load_dotenv
import openai
from urllib.parse import urlparse

# Crawl4AI imports
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
from fpdf import FPDF

# LangChain & vectorstore imports
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

# Providers
from langchain_community.chat_models import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

# Load .env for overrides
load_dotenv()

# === Load configuration ===
CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")
if not os.path.exists(CONFIG_PATH):
    print(f"Configuration file not found: {CONFIG_PATH}")
    sys.exit(1)
with open(CONFIG_PATH) as f:
    cfg = yaml.safe_load(f)

PROVIDER = cfg.get("provider", "openai").lower()
if PROVIDER not in {"openai", "gemini", "deepseek"}:
    raise ValueError("provider must be 'openai', 'gemini', or 'deepseek'")

MODEL_NAME = None

def _set_openai_env(key: str, base: str = None):
    openai.api_key = key
    os.environ["OPENAI_API_KEY"] = key
    if base:
        openai.api_base = base
        os.environ["OPENAI_API_BASE"] = base

def setup_provider():
    global MODEL_NAME
    if PROVIDER == "openai":
        openai_cfg = cfg.get("openai", {})
        api_key = openai_cfg.get("api_key")
        if not api_key:
            raise ValueError("OpenAI API key not found in config.yaml")
        _set_openai_env(api_key)
        MODEL_NAME = openai_cfg.get("model", "gpt-4")
        if MODEL_NAME.startswith("gpt-4."):
            MODEL_NAME = "gpt-4"
    elif PROVIDER == "gemini":
        gemini_cfg = cfg.get("gemini", {})
        api_key = gemini_cfg.get("api_key")
        if not api_key:
            raise ValueError("Gemini API key not found in config.yaml")
        os.environ["GOOGLE_API_KEY"] = api_key
        MODEL_NAME = gemini_cfg.get("model", "gemini-2.5-flash")
    else:  # deepseek
        deep_cfg = cfg.get("deepseek", {})
        api_key = deep_cfg.get("api_key")
        base_url = deep_cfg.get("base_url")
        if not api_key or not base_url:
            raise ValueError("DeepSeek API key or base_url not found in config.yaml")
        _set_openai_env(api_key, base_url)
        gemini_key = cfg.get("gemini", {}).get("api_key")
        if gemini_key:
            os.environ["GOOGLE_API_KEY"] = gemini_key
        MODEL_NAME = deep_cfg.get("model", "deepseek-chat")

async def crawl_site(url: str, depth: int, pdf_filename: str):
    page_results = []
    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(max_depth=depth, include_external=False),
        scraping_strategy=LXMLWebScrapingStrategy(),
        wait_for_images=True,
        scan_full_page=True,
        scroll_delay=3,
        verbose=False
    )
    print(f"Starting crawl for {url} (depth={depth})...")
    try:
        async with AsyncWebCrawler() as crawler:
            results = await crawler.arun(url, config=config)
            for res in results:
                text = getattr(res.markdown, 'raw_markdown', '')
                if text:
                    page_results.append(text)
    except Exception as e:
        print(f"Error during crawling: {e}")
        sys.exit(1)
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    if page_results:
        combined = "\n\n---\n\n".join(page_results)
        safe_text = combined.encode('latin-1', errors='replace').decode('latin-1')
        pdf.multi_cell(0, 10, safe_text)
    else:
        pdf.multi_cell(0, 10, "No content available.")
    pdf.output(pdf_filename)
    print(f"PDF saved as {pdf_filename}")

def setup_rag_pipeline(url: str, depth: int):
    domain = urlparse(url).netloc.replace(':', '_') or 'output'
    pdf_filename = f"{domain}.pdf"
    
    if not os.path.exists(pdf_filename):
        asyncio.run(crawl_site(url, depth, pdf_filename))
    
    loader = PyPDFLoader(pdf_filename)
    docs = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = splitter.split_documents(docs)
    
    if PROVIDER == "openai":
        embeddings = OpenAIEmbeddings(model="text-embedding-ada-002")
    else:
        embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
    
    if chunks:
        vectorstore = Chroma.from_documents(documents=chunks, embedding=embeddings)
        retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 5})
    else:
        retriever = None
    
    if PROVIDER == "gemini":
        llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=0.3)
    else:
        llm = ChatOpenAI(model=MODEL_NAME, temperature=0.3)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are Alice, a smart, friendly AI sales assistant for our AI services website.Use the provided website data to answer questions.If you lack info, reply the information is unavailable.Speak like a helpful, skilled seller.Keep sentences short.\n\n{context}"),
        ("human", "{input}")
    ])
    
    if retriever:
        qa_chain = create_stuff_documents_chain(llm, prompt)
        return create_retrieval_chain(retriever, qa_chain)
    else:
        qa_chain = create_stuff_documents_chain(llm, ChatPromptTemplate.from_messages([
            ("system", "No information is available to answer this question."),
            ("human", "{input}")
        ]))
        return create_retrieval_chain(None, qa_chain)

if __name__ == "__main__":
    setup_provider()
    rag_chain = setup_rag_pipeline("http://127.0.0.1:5000", 3)
    print(f"RAG Agent running with provider: {PROVIDER}")
    while True:
        q = input("Enter your question (or 'exit'): ").strip()
        if q.lower() in {'exit', 'quit'}:
            break
        try:
            resp = rag_chain.invoke({"input": q})
            print("Answer:", resp.get('answer', resp))
        except Exception as e:
            print(f"Error during invocation: {e}")