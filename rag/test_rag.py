from llama_index.core import VectorStoreIndex, SimpleDirectoryReader
from llama_index.llms.ollama import Ollama
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.core import Settings

# Указываем модели
# было
Settings.llm = Ollama(model="qwen2.5:7b-instruct", request_timeout=120.0)

# стало
Settings.llm = Ollama(
    model="llama3.2:3b",
    request_timeout=120.0,
    system_prompt="Ты SEO-эксперт. ВАЖНО: отвечай ТОЛЬКО на русском языке. Никогда не используй китайский или любой другой язык кроме русского. Давай конкретные практические рекомендации."
)
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")

# Загружаем документы
documents = SimpleDirectoryReader("rag/docs").load_data()
print(f"Загружено документов: {len(documents)}")

# Строим индекс
index = VectorStoreIndex.from_documents(documents)

# Тестовый запрос
query_engine = index.as_query_engine()
response = query_engine.query("Какие основные рекомендации по SEO оптимизации?")
print("\nОтвет:")
print(response)