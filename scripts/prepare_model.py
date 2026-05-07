
import os
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# -----------------------------------------------------------------------------
# 1. EMBEDDING MODEL (SentenceTransformer)
# -----------------------------------------------------------------------------
# Define the model name and the desired output directory
model_name = 'intfloat/multilingual-e5-small'
output_path = 'models/e5-small-sanitized'

# Ensure the output directory exists
os.makedirs(output_path, exist_ok=True)

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

print(f"🚀 Loading NLI model '{nli_model_name}' (Online)...")
# Ensure the output directory exists
os.makedirs(nli_output_path, exist_ok=True)

tokenizer = AutoTokenizer.from_pretrained(nli_model_name)
model_nli = AutoModelForSequenceClassification.from_pretrained(nli_model_name)

print(f"💾 Saving NLI model to '{nli_output_path}'...")
tokenizer.save_pretrained(nli_output_path)
model_nli.save_pretrained(nli_output_path)
print("✅ NLI model saved.")
