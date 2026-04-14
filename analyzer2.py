from pydantic import BaseModel, Field
from google import genai
from google.genai import types
import sys
import os
import os.path
import time

# 1. Define your "Slots"
class LouisianaConnection(BaseModel):
    who: str = Field(description="Name of the person or family")
    what: str = Field(description="The nature of the connection to MIT")
    time_period: str = Field(description="The relevant years or era")
    page_numbers: list[int] = Field(
        description="A list of 1-based page numbers in the PDF where this information appears"
    )

class AnalysisResult(BaseModel):
    path: str | None = None
    processing_time: float | None = None
    score: int = Field(ge=0, le=10, description="Probability score")
    connections: list[LouisianaConnection]

# 2. Initialize Client
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# NEW: Get the filename from the command line
if len(sys.argv) < 2:
    print("Usage: python analyzer2.py <path_to_pdf>")
    sys.exit(1)

pdf_path = sys.argv[1]

# NEW: Read the local file into memory
with open(pdf_path, "rb") as f:
    pdf_bytes = f.read()

# NEW: Create the 'Part' object Gemini expects
file_part = types.Part.from_bytes(
    data=pdf_bytes,
    mime_type="application/pdf"
)
# Apply the resolution to this specific part
# Note: For Gemini 3, this is currently an attribute of the Part
file_part.media_resolution = {"level": "media_resolution_high"}

# 3. Fill the Slots
t0 = time.time()
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=["Analyze the attached report for Louisiana connections...",file_part],
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=AnalysisResult,  # Directly pass the class
    ),
)
t1 = time.time()

# 4. Access parsed data (already a Pydantic object)
data = response.parsed
print(f"Filename: {os.path.basename(pdf_path)} Score: {data.score}")
for conn in data.connections:
    print(f"- {conn.who}: {conn.what} ({conn.time_period}) ({conn.page_numbers})")

usage = response.usage_metadata
print(f"Prompt Tokens: {usage.prompt_token_count}")
print(f"Candidates Tokens: {usage.candidates_token_count}")
print(f"Total Tokens: {usage.total_token_count}")
print(f"Total Time: {t1-t0}")

with open("cost.csv","a") as f:
    f.write(f"{usage.prompt_token_count},{usage.candidates_token_count},{usage.total_token_count}\n")

with open("output.jsons","a") as f:
    data = response.parsed
    data.path = pdf_path
    data.processing_time = t1-t0
    print(data.model_dump_json(indent=2),file=f)
