from pathlib import Path
import io
import gc

import torch
import fitz  # PyMuPDF
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

MODEL_ID = "Qwen/Qwen2.5-VL-7B-Instruct"

PDF_PATH = r"C:\Users\ghoniem\Downloads\2510.08731v1.pdf"
OUT_DIR = Path(r"D:\etl\SERA-AI\app\blueprints\data\chunks")

DPI = 150
BATCH_SIZE = 2
MAX_NEW_TOKENS = 1024


SYSTEM_PROMPT = """\
You are a document parser that converts document page images to structured markdown.

For each page image you receive:

TEXT:
- Extract all text in reading order
- Preserve Arabic (RTL) text exactly, do not translate
- Use # ## ### for headings based on visual size/weight

TABLES:
- Convert to markdown table format
- Preserve all cell values exactly

FIGURES & CHARTS:
- Start with [Figure]: or [Chart]:
- Describe type
- Extract ALL visible values, labels, axis titles, legend items
- Describe the trend or key insight

CAPTIONS:
- Keep figure/table captions attached to their element

OUTPUT:
- Return ONLY markdown, no explanation
- Preserve document language
"""


# ─────────────────────────────────────────────
# Device setup
# ─────────────────────────────────────────────

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {device}")

if device == "cuda":
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.set_float32_matmul_precision("high")


# ─────────────────────────────────────────────
# Model setup
# ─────────────────────────────────────────────

print(f"Loading model: {MODEL_ID}")

processor = AutoProcessor.from_pretrained(
    MODEL_ID,
    use_fast=True,
)

model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    MODEL_ID,
    dtype=torch.float16 if device == "cuda" else torch.float32,
    device_map="auto",
    attn_implementation="sdpa",  # Windows-safe. Do not use flash_attention_2.
)

model.eval()

print("Model loaded.\n")


# ─────────────────────────────────────────────
# PDF rendering
# ─────────────────────────────────────────────

def pdf_pages_to_images(pdf_path: str, dpi: int = 150) -> list[Image.Image]:
    images: list[Image.Image] = []

    with fitz.open(pdf_path) as doc:
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)

        for page in doc:
            pix = page.get_pixmap(
                matrix=mat,
                colorspace=fitz.csRGB,
                alpha=False,
            )

            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            images.append(img)

    return images


# ─────────────────────────────────────────────
# Prompt building
# ─────────────────────────────────────────────

def build_prompt(page_image: Image.Image) -> str:
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": [
                {"type": "image", "image": page_image},
                {"type": "text", "text": "Parse this document page to markdown."},
            ],
        },
    ]

    return processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )


# ─────────────────────────────────────────────
# Page parsing
# ─────────────────────────────────────────────

def move_inputs_to_device(inputs: dict) -> dict:
    target_device = model.device

    moved = {}
    for key, value in inputs.items():
        if hasattr(value, "to"):
            moved[key] = value.to(target_device)
        else:
            moved[key] = value

    return moved


def parse_pages_batch(
    images: list[Image.Image],
    max_new_tokens: int = MAX_NEW_TOKENS,
) -> list[str]:
    prompts = [build_prompt(img) for img in images]

    inputs = processor(
        text=prompts,
        images=images,
        padding=True,
        return_tensors="pt",
    )

    inputs = move_inputs_to_device(inputs)

    input_len = inputs["input_ids"].shape[1]

    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            use_cache=True,
            pad_token_id=processor.tokenizer.eos_token_id,
        )

    generated = outputs[:, input_len:]

    decoded = processor.batch_decode(
        generated,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )

    return [text.strip() for text in decoded]


# ─────────────────────────────────────────────
# Full PDF pipeline
# ─────────────────────────────────────────────

def parse_pdf_to_markdown(
    pdf_path: str,
    batch_size: int = BATCH_SIZE,
    dpi: int = DPI,
) -> list[dict]:
    print(f"Rendering PDF pages at {dpi} DPI...")
    images = pdf_pages_to_images(pdf_path, dpi=dpi)

    total = len(images)
    print(f"Parsing {total} pages in batches of {batch_size}\n")

    results: list[dict] = []

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = images[start:end]

        print(f"Pages {start + 1}-{end}/{total} ...", flush=True)

        try:
            markdowns = parse_pages_batch(batch)

        except torch.cuda.OutOfMemoryError:
            print("CUDA OOM. Retrying this batch page-by-page...")
            torch.cuda.empty_cache()
            gc.collect()

            markdowns = []

            for img in batch:
                single_result = parse_pages_batch([img])
                markdowns.extend(single_result)

                if device == "cuda":
                    torch.cuda.empty_cache()
                gc.collect()

        for i, markdown in enumerate(markdowns):
            page_num = start + i + 1

            results.append(
                {
                    "page": page_num,
                    "markdown": markdown,
                }
            )

            preview = markdown[:120].replace("\n", " ")
            print(f"  Page {page_num} → {preview}...")

        if device == "cuda":
            torch.cuda.empty_cache()

        gc.collect()

    return results


# ─────────────────────────────────────────────
# Save output
# ─────────────────────────────────────────────

def save_pages(pages: list[dict], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    for page in pages:
        page_num = page["page"]
        markdown = page["markdown"]

        path = out_dir / f"page_{page_num:03d}.md"
        path.write_text(markdown, encoding="utf-8")

        print(f"Saved {path}")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    pages = parse_pdf_to_markdown(
        pdf_path=PDF_PATH,
        batch_size=BATCH_SIZE,
        dpi=DPI,
    )

    save_pages(pages, OUT_DIR)

    print("\n✓ PDF parsing complete.")