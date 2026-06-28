import os
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import AuthorizedSession

API_BASE = "https://www.googleapis.com/blogger/v3"

def get_blogger_session() -> AuthorizedSession:
    """
    Returns an authorized requests session for the Blogger API.
    Looks for credentials in environment variables first (BLOGGER_REFRESH_TOKEN,
    BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET). Fallback: local 'blogger_token.json'.
    """
    refresh_token = os.environ.get("BLOGGER_REFRESH_TOKEN")
    client_id = os.environ.get("BLOGGER_CLIENT_ID")
    client_secret = os.environ.get("BLOGGER_CLIENT_SECRET")

    if refresh_token and client_id and client_secret:
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret
            )
            return AuthorizedSession(creds)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize credentials from Blogger env vars: {e}")

    # Fallback to local file blogger_token.json (path-independent)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)
    token_paths = [
        "blogger_token.json",
        os.path.join(script_dir, "blogger_token.json"),
        os.path.join(parent_dir, "blogger_token.json")
    ]
    token_file = None
    for p in token_paths:
        if os.path.exists(p):
            token_file = p
            break

    if token_file:
        try:
            creds = Credentials.from_authorized_user_file(token_file)
            return AuthorizedSession(creds)
        except Exception as e:
            raise RuntimeError(f"Failed to load credentials from '{token_file}': {e}")

    raise FileNotFoundError(
        "Blogger credentials not found. Please set BLOGGER_REFRESH_TOKEN, "
        "BLOGGER_CLIENT_ID, and BLOGGER_CLIENT_SECRET environment variables or "
        "provide a 'blogger_token.json' file via oauth_setup.py."
    )

def fetch_post(session: AuthorizedSession, blog_id: str, post_id: str) -> dict:
    """
    Fetches a post's metadata and content.
    """
    url = f"{API_BASE}/blogs/{blog_id}/posts/{post_id}"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()

def fetch_all_posts(session: AuthorizedSession, blog_id: str) -> dict:
    """
    Fetches all posts in the blog (returns list of posts with id, url, content, etc.).
    """
    url = f"{API_BASE}/blogs/{blog_id}/posts"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()

def update_post(session: AuthorizedSession, blog_id: str, post_id: str, new_content: str) -> dict:
    """
    Updates/patches a post's content.
    """
    url = f"{API_BASE}/blogs/{blog_id}/posts/{post_id}"
    payload = {"content": new_content}
    resp = session.patch(url, json=payload)
    resp.raise_for_status()
    return resp.json()

def fetch_page(session: AuthorizedSession, blog_id: str, page_id: str) -> dict:
    """
    Fetches a page's metadata and content.
    """
    url = f"{API_BASE}/blogs/{blog_id}/pages/{page_id}"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()

def update_page(session: AuthorizedSession, blog_id: str, page_id: str, new_content: str) -> dict:
    """
    Updates/patches a page's content.
    """
    url = f"{API_BASE}/blogs/{blog_id}/pages/{page_id}"
    payload = {"content": new_content}
    resp = session.patch(url, json=payload)
    resp.raise_for_status()
    return resp.json()

if __name__ == "__main__":
    print("Testing blogger_module.py imports...")
    try:
        session = get_blogger_session()
        print("Session initialized successfully.")
    except Exception as e:
        print(f"Auth check (expected if no credentials present): {e}")
