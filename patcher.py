import re
import json

def patch_slot_html(content: str, new_iframe_url: str) -> str:
    """
    Updates the iframe source within the SLOT_IFRAME_START and SLOT_IFRAME_END markers in Blog A's slot post.
    Supports flexible whitespace, newlines, and case-insensitivity.
    """
    pattern = re.compile(
        r'(<!--\s*SLOT_IFRAME_START\s*-->).*?(<!--\s*SLOT_IFRAME_END\s*-->)',
        re.DOTALL | re.IGNORECASE
    )
    
    # Check if the markers exist in the page HTML
    if not pattern.search(content):
        raise ValueError("Could not find <!--SLOT_IFRAME_START--> and <!--SLOT_IFRAME_END--> markers in post HTML.")
        
    replacement = (
        r'\1\n'
        f'<iframe allowfullscreen="true" frameborder="0" height="500px" scrolling="1" src="{new_iframe_url}" width="100%"></iframe>\n'
        r'\2'
    )
    
    return pattern.sub(replacement, content)

def find_array_span(text: str, marker: str = "const matches"):
    """
    Walks forward from 'const matches' to find the opening '[' and matching ']'.
    Returns (start_idx, end_idx_exclusive) of the array literal.
    """
    start_marker = text.find(marker)
    if start_marker == -1:
        raise ValueError(f"Could not find '{marker}' in given text")

    bracket_start = text.find("[", start_marker)
    if bracket_start == -1:
        raise ValueError(f"Could not find opening '[' after '{marker}'")

    depth = 0
    i = bracket_start
    in_string = False
    string_char = ""
    escape = False

    while i < len(text):
        char = text[i]

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == string_char:
                in_string = False
        else:
            if char in ('"', "'"):
                in_string = True
                string_char = char
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    return bracket_start, i + 1
        i += 1

    raise ValueError(f"Reached end of text without closing the array after '{marker}'")

def patch_matches_page(content: str, matches: list) -> str:
    """
    Splices the new matches array into the event page's matches variable.
    """
    start, end = find_array_span(content)
    new_array_text = json.dumps(matches, indent=2, ensure_ascii=False)
    return content[:start] + new_array_text + content[end:]

if __name__ == "__main__":
    # Test block
    print("Testing patcher.py functions...")
    
    # Test slot patching
    blog_a_html = """
    <div>
       Some header content
       <!--SLOT_IFRAME_START-->
       <iframe allowfullscreen="true" frameborder="0" height="500px" scrolling="1" src="#" width="100%"></iframe>
       <!--SLOT_IFRAME_END-->
       Some footer content
    </div>
    """
    patched_slot = patch_slot_html(blog_a_html, "https://ex.roooom.online/?alba-player=home1")
    print("Patched Slot HTML:")
    print(patched_slot)
    
    # Test page patching
    blog_b_html = """
    <script>
      const matches = [
        {"id": 1, "team1": "Algeria"}
      ];
    </script>
    """
    patched_page = patch_matches_page(blog_b_html, [{"id": 2, "team1": "Tunisia"}])
    print("\nPatched Page HTML:")
    print(patched_page)
