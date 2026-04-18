import os
import sys

from google import genai


def list_models_live(api_key: str) -> None:
    client = genai.Client(api_key=api_key)

    print(f"{'MODEL ID':<40} | {'INPUT LIMIT':<12} | {'OUTPUT LIMIT':<12} | {'CAPABILITIES'}")
    print("-" * 95)

    try:
        # 2. Fetch the live list from Google
        # We convert to a list so we can sort them by name
        all_models = list(client.models.list())
        all_models.sort(key=lambda x: getattr(x, 'name', ''))

        for m in all_models:
            # 3. SAFETY CHECKS (Defensive Coding)
            # The SDK attributes can vary by version, so we use getattr() to avoid crashes.

            name = getattr(m, 'name', 'Unknown').replace("models/", "")


            # Get Limits safely
            in_limit = getattr(m, 'input_token_limit', 0)
            out_limit = getattr(m, 'output_token_limit', 0)

            # Format numbers (handle None or 0)
            in_str = f"{in_limit:,}" if in_limit else "?"
            out_str = f"{out_limit:,}" if out_limit else "?"

            # Get other capabilities safely
            capabilities = []
            if getattr(m, 'thinking', False):
                capabilities.append("Thinking")
            if getattr(m, 'vision', False) or "image" in name: # Fallback check for vision
                capabilities.append("Vision")

            cap_str = ", ".join(capabilities) if capabilities else "Text"

            print(f"{name:<40} | {in_str:<12} | {out_str:<12} | {cap_str}")

    except Exception as e:
        print(f"\nAPI Error: {e}")
        # Debugging aid: If it crashes, show what the object actually looks like
        print("\nDEBUG: First model object attributes:")
        try:
            print(dir(all_models[0]))
        except Exception:
            pass


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY environment variable not found.", file=sys.stderr)
        return 1
    list_models_live(api_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
