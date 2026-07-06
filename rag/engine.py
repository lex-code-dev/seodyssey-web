import os
import json
import chromadb
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, StorageContext
from llama_index.core import Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.llms.openai import OpenAI
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore
import urllib.request
from html.parser import HTMLParser
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, "docs")
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_storage")
COLLECTION_NAME = "seodyssey_knowledge"

Settings.llm = OpenAI(
    model="gpt-4o-mini",
    temperature=0.3,
    system_prompt="""Ты опытный SEO-консультант. Отвечай ТОЛЬКО на русском языке.
Твой ответ — это ТОЛЬКО валидный JSON, без каких-либо пояснений, без markdown, без обёртки в ```json.

Формат ответа:
{
  "intro": "1-2 предложения: что всё решаемо, ничего критичного. Тёплый, поддерживающий тон.",
  "recommendations": [
    {
      "title": "Короткое название проблемы",
      "severity": "warn",
      "summary": "1-2 предложения: почему важно, без страшилок. Добавь фразу-поддержку.",
      "steps": [
        "Конкретный шаг 1",
        "Конкретный шаг 2",
        "Конкретный шаг 3"
      ]
    }
  ]
}

Правила:
- severity может быть только "warn" или "fail"
- Включай ВСЕ проблемы из списка без исключений — каждая проблема должна стать отдельной рекомендацией
- Снижение трафика — всегда важно, даже если процент небольшой, всегда включай
- Если несколько проблем об одном и том же — объедини их в одну рекомендацию
- В шагах давай конкретику: какой инструмент использовать, где найти, как проверить
- Для sitemap: укажи как сгенерировать (Yoast, Google XML Sitemaps, или вручную), где разместить, как добавить в Вебмастер
- Для meta description: дай 3 отдельных шага, каждый — один готовый пример description для конкретного сайта. Формат: 'Вариант N: "текст"'. Каждый пример 50-160 символов.
- Для Title: дай 2-3 готовых варианта Title в отдельных шагах. Формат: 'Вариант N: "текст"'. 50-60 символов.
- Не добавляй ничего кроме JSON"""
)
Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")
Settings.transformations = [SentenceSplitter(chunk_size=512, chunk_overlap=50)]

_index_cache = None


class SiteParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title = ''
        self.description = ''

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        if tag == 'meta':
            attrs_dict = dict(attrs)
            if attrs_dict.get('name', '').lower() == 'description':
                self.description = attrs_dict.get('content', '')

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def fetch_site_info(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')
        p = SiteParser()
        p.feed(html)
        return {'title': p.title.strip(), 'description': p.description.strip()}
    except Exception:
        return {'title': '', 'description': ''}


def get_index():
    global _index_cache
    if _index_cache is not None:
        return _index_cache

    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = chroma_client.get_or_create_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=collection)

    if collection.count() == 0:
        print("Коллекция пустая — индексируем документы...")
        documents = SimpleDirectoryReader(DOCS_DIR, recursive=True).load_data()
        print(f"Загружено документов: {len(documents)}")
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_documents(
            documents,
            storage_context=storage_context,
            show_progress=True
        )
        print("Индексация завершена.")
    else:
        print(f"Загружаем существующий индекс ({collection.count()} чанков)...")
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    _index_cache = index
    return index


def get_recommendations(check_data: dict) -> dict:
    domain = check_data.get("url", "неизвестный сайт")
    site_info = fetch_site_info(domain)
    parts = []
    if site_info['title']:
        parts.append(f"Title сайта: {site_info['title']}")
    if site_info['description']:
        parts.append(f"Description: {site_info['description']}")
    site_context = "\n".join(parts)

    problems = []
    checks = check_data.get("checks", {})
    for key, val in checks.items():
        if not isinstance(val, dict):
            continue
        status = val.get("status", "")

        if key == "webmaster" and "checks" in val:
            for sub_key, sub_val in val["checks"].items():
                if not isinstance(sub_val, dict):
                    continue
                summary = sub_val.get("summary", {})
                title = summary.get("title", sub_key)
                severity = sub_val.get("external_severity", "")
                problems.append(f"- {title} (важность: {severity})")
            continue

        # metrics — пропускаем, трафик уже есть отдельно в traffic
        if key == "metrics":
            continue

        if status in ("warning", "warn", "error", "fail"):
            summary = val.get("summary", {})
            title = summary.get("title") or val.get("label") or key
            lines = summary.get("lines", [])
            if key == "traffic":
                delta = val.get("delta_pct")
                prev = val.get("prev")
                cur = val.get("current")
                problems.append(f"- Снижение поискового трафика: {prev} → {cur} визитов ({delta}%)")
            else:
                problems.append(f"- {title}: {'; '.join(lines)}")
        elif status == "skipped":
            reason = val.get("reason", "")
            problems.append(f"- {key}: пропущено ({reason})")

    # Дедупликация похожих проблем
    seen = set()
    unique_problems = []
    for p in problems:
        dedup_key = p.lower()[:40]
        if dedup_key not in seen:
            seen.add(dedup_key)
            unique_problems.append(p)
    problems = unique_problems

    # problems_text формируется ПОСЛЕ дедупликации
    problems_text = "\n".join(problems) if problems else "Явных проблем не обнаружено."

    query = f"""
Сайт: {domain}
{site_context}

Результаты проверки:
{problems_text}

Верни JSON строго по проблемам из списка выше. Каждая проблема — отдельная рекомендация.
"""

    index = get_index()
    engine = index.as_query_engine()
    response = str(engine.query(query)).strip()

    # Убираем возможную обёртку ```json ... ```
    if response.startswith("```"):
        response = response.split("```")[1]
        if response.startswith("json"):
            response = response[4:]
        response = response.strip()

    try:
        return json.loads(response)
    except Exception:
        return {
            "intro": "",
            "recommendations": [
                {
                    "title": "Рекомендации",
                    "severity": "warn",
                    "summary": response[:300],
                    "steps": []
                }
            ]
        }
