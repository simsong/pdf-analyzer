import argparse
import json
import logging
import os
import sys
import google.generativeai as genai

# Setup logging with printf-style formatting
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

class MITReportAnalyzer:
    """Analyzes MIT historical reports for specific state connections."""

    SYSTEM_INSTRUCTION = (
        "You are a precise archival researcher. Analyze the provided PDF and "
        "determine the probability (0-10) that it contains significant "
        "connections to the specified state. Return ONLY a JSON object."
    )

    JSON_TEMPLATE = {
        "score": 0,
        "reasoning": "",
        "entities": [
            {"who": "", "what": "", "time_period": "", "description": ""}
        ]
    }

    def __init__(self, api_key: str, model_name: str = "gemini-1.5-flash"):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(
            model_name=model_name,
            system_instruction=self.SYSTEM_INSTRUCTION
        )

    def analyze(self, file_path: str, state: str) -> dict:
        """Uploads file and queries the model."""
        if not os.path.exists(file_path):
            logger.error("File not found: %s", file_path)
            sys.exit(1)

        logger.info("Uploading %s to Gemini API...", file_path)
        uploaded_file = genai.upload_file(path=file_path)

        prompt = (
            f"Analyze this MIT President's Report for connections to {state}. "
            "Provide a score from 0 to 10 on the probability of important connections "
            "(families, citations, or institutions). If the score is > 7, "
            "populate the 'entities' list with the top 3 matches. "
            f"Output must strictly follow this JSON schema: {json.dumps(self.JSON_TEMPLATE)}"
        )

        try:
            response = self.model.generate_content(
                [uploaded_file, prompt],
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error("API Failure for %s: %s", file_path, e)
            return {}
        finally:
            # Cleanup: Remote files are deleted automatically after 48h,
            # but you can delete manually here if desired.
            pass

def main():
    parser = argparse.ArgumentParser(description="Analyze MIT reports via Gemini API.")
    parser.add_argument("pdf", help="Path to the PDF file")
    parser.add_argument("--state", default="Louisiana", help="State to focus on")
    parser.add_argument("--key", help="Gemini API Key (or use GEMINI_API_KEY env)")

    args = parser.parse_args()
    api_key = args.key or os.environ.get("GEMINI_API_KEY")

    if not api_key:
        logger.error("No API key provided via --key or GEMINI_API_KEY environment variable.")
        sys.exit(1)

    analyzer = MITReportAnalyzer(api_key)
    result = analyzer.analyze(args.pdf, args.state)

    # Output to stdout for redirection/piping
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
