from dotenv import load_dotenv
load_dotenv()  # 必须在所有 os.getenv() 之前调用
from langchain_community.document_loaders import DirectoryLoader, PyPDFLoader
from langchain_text_splitters import CharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_classic.chains import RetrievalQA
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
import os
from dotenv import load_dotenv
load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OPENAI_BASE_URL = os.getenv('OPENAI_BASE_URL')

def should_use_langchain():
    return OPENAI_BASE_URL is not None

def read_resumes():
    d_loader = DirectoryLoader("./resume", glob="*.pdf", loader_cls=PyPDFLoader)
    pdf_pages = d_loader.load()
    resume_text = ""
    for page in pdf_pages:
        resume_text += page.page_content
    return resume_text

def get_text_chunks(text):
    text_splitter = CharacterTextSplitter(
        separator="\n",
        chunk_size=2000,
        chunk_overlap=200,
        length_function=len
    )
    return text_splitter.split_text(text)

def get_vectorstore(text_chunks):
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs={'device': 'cpu'},
        encode_kwargs={'normalize_embeddings': True}
    )
    vectorstore = FAISS.from_texts(texts=text_chunks, embedding=embeddings)
    return vectorstore

def generate_letter(vectorstore, job_description):
    character_limit = 300
    langchain_prompt_template = f"""
        你将扮演一位求职者的角色,根据上下文里的简历内容以及应聘工作的描述,来直接给HR写一个礼貌专业, 且字数严格限制在{character_limit}以内的求职消息,要求能够用专业的语言结合简历中的经历和技能,并结合应聘工作的描述,来阐述自己的优势,尽最大可能打动招聘者。始终使用中文来进行消息的编写。开头是招聘负责人, 结尾附上求职者联系方式。这是一份求职消息，不要包含求职内容以外的东西,以便于我直接自动化复制粘贴发送。
        工作描述
        {job_description}"""+"""
        简历内容:
        {context}
        要求:
        {question} 
    """
    question = "根据工作描述，寻找出简历里最合适的技能都有哪些?求职者的优势是什么?"
    PROMPT = PromptTemplate.from_template(langchain_prompt_template)
    llm = ChatOpenAI(
        model="deepseek-chat",
        temperature=0.2,
        openai_api_base=OPENAI_BASE_URL,
        openai_api_key=OPENAI_API_KEY
    )
    qa_chain = RetrievalQA.from_chain_type(
        llm,
        retriever=vectorstore.as_retriever(),
        chain_type_kwargs={"prompt": PROMPT}
    )
    result = qa_chain({"query": question})
    letter = result['result'].replace('\n', ' ')
    return letter