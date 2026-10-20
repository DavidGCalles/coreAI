# Requisitos del entorno de hidratación: pip install -r scripts/requirements.txt
# (torch se instala aparte según CPU/GPU del host).

import os
from sentence_transformers import SentenceTransformer
from transformers import AutoModel, AutoModelForSequenceClassification, AutoProcessor, AutoTokenizer


def already_prepared(output_dir):
    """True si el modelo ya está hidratado en el contexto de build (se salta la descarga)."""
    return os.path.isdir(output_dir) and len(os.listdir(output_dir)) > 0


# -----------------------------------------------------------------------------
# 1. EMBEDDING MODEL (SentenceTransformer)
# -----------------------------------------------------------------------------
# Define the model name and the desired output directory
model_name = 'intfloat/multilingual-e5-small'
output_path = 'models/e5-small-sanitized'

# Ensure the output directory exists
os.makedirs(output_path, exist_ok=True)

if already_prepared(output_path):
    print(f"⏭️ Embedding model '{model_name}' ya hidratado en '{output_path}' — saltando.")
else:
    print(f"🚀 Loading embedding model '{model_name}' (Online)...")
    # Load the model from Hugging Face
    model = SentenceTransformer(model_name)

    print(f"💾 Saving sanitized embedding model to '{output_path}'...")
    # Save the model to the specified directory
    model.save(output_path)

    print("✅ Embedding model saved.")

    # The original Dockerfile also removed modules.json.
    # While this might not be necessary when the model is pre-packaged,
    # we'll replicate the behavior for consistency.
    modules_file_path = os.path.join(output_path, 'modules.json')
    if os.path.exists(modules_file_path):
        print(f"🗑️ Removing '{modules_file_path}' for offline compatibility...")
        os.remove(modules_file_path)
        print("✅ File removed.")

# -----------------------------------------------------------------------------
# 2. NLI MODEL (Zero-Shot Classification)
# -----------------------------------------------------------------------------
#nli_model_name = 'MoritzLaurer/mDeBERTa-v3-base-mnli-xnli'
nli_model_name = "MoritzLaurer/DeBERTa-v3-large-mnli-fever-anli-ling-wanli"
nli_output_path = 'models/nli-model'

# Ensure the output directory exists
os.makedirs(nli_output_path, exist_ok=True)

if already_prepared(nli_output_path):
    print(f"⏭️ NLI model '{nli_model_name}' ya hidratado en '{nli_output_path}' — saltando.")
else:
    print(f"🚀 Loading NLI model '{nli_model_name}' (Online)...")

    tokenizer = AutoTokenizer.from_pretrained(nli_model_name)
    model_nli = AutoModelForSequenceClassification.from_pretrained(nli_model_name)

    print(f"💾 Saving NLI model to '{nli_output_path}'...")
    tokenizer.save_pretrained(nli_output_path)
    model_nli.save_pretrained(nli_output_path)
    print("✅ NLI model saved.")

# -----------------------------------------------------------------------------
# 3. IMAGE EMBEDDING (ADR-007-001) — canario + candidato
# -----------------------------------------------------------------------------
# Canario: TinyCLIP (CLIPModel nativo de transformers, 8M params, riesgo cero).
# Candidato: jina-clip-v2 (licencia cc-by-nc-4.0 — valla comercial diferida,
# decisión de David). Requiere trust_remote_code=True: el save_pretrained
# copia los .py de modelado propios de Jina al directorio local.
# Fallback si Infinity se atraganta con el remote code: google/siglip2-base-patch16-256.
for image_model_name, image_output_path in [
    ("wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M", "models/tinyclip"),
    ("jinaai/jina-clip-v2", "models/jina-clip-v2"),
]:
    os.makedirs(image_output_path, exist_ok=True)

    if already_prepared(image_output_path):
        print(f"⏭️ Image embedding model '{image_model_name}' ya hidratado en '{image_output_path}' — saltando.")
        continue

    print(f"🚀 Loading image embedding model '{image_model_name}' (Online)...")
    image_model = AutoModel.from_pretrained(image_model_name, trust_remote_code=True)
    image_tokenizer = AutoTokenizer.from_pretrained(image_model_name, trust_remote_code=True)
    image_processor = AutoProcessor.from_pretrained(image_model_name, trust_remote_code=True)

    print(f"💾 Saving image embedding model to '{image_output_path}'...")
    image_model.save_pretrained(image_output_path)
    image_tokenizer.save_pretrained(image_output_path)
    image_processor.save_pretrained(image_output_path)
    print(f"✅ Image embedding model saved to '{image_output_path}'.")
