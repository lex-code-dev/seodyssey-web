import os
import httpx

GENSEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/gen/search"
FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "b1guvrpunctfrsd8jlit")


def test_gensearch(query: str = "лучший инструмент для SEO аудита"):
    api_key = os.getenv("YANDEX_WORDSTAT_API_KEY")
    if not api_key:
        print("ERROR: YANDEX_WORDSTAT_API_KEY не задан")
        return

    response = httpx.post(
        GENSEARCH_URL,
        headers={
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "query": query,
            "folderId": FOLDER_ID,
        },
        timeout=15.0,
    )

    print("Status:", response.status_code)
    import json
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_gensearch()