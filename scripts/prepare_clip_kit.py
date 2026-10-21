#!/usr/bin/env python3
# prepare_clip_kit.py — cirugía del kit CLIP para carga 100% offline (ADR-007-001, #49).
#
# Lo invoca prepare_model.py DESPUÉS de hidratar los kits de imagen (sección 3),
# y también se puede ejecutar a mano:  python scripts/prepare_clip_kit.py [--force]
# Ejecutar desde la raíz del repo (mismas rutas relativas que prepare_model.py).
#
# Por qué existe (receta validada 21-oct-2026, Infinity 0.0.78 + transformers 4.51.3):
#
#   1. jina-clip-v2 es remote-code y save_pretrained NO copia los .py de la
#      implementación (jina-clip-implementation): hay que traerlos al kit y
#      reescribir el auto_map en forma RELATIVA para que resuelvan offline.
#   2. El text tower del jina (hf_model.py) carga su config desde
#      text_config.hf_model_name_or_path → jinaai/jina-embeddings-v3 en el Hub.
#      Se hornea DENTRO del kit (config + tokenizer + .py del xlm-roberta-flash);
#      los PESOS no: viajan en el checkpoint del propio jina
#      (load_trained_adapters=False, kwargs del propio config del jina).
#   3. transformers solo copia al caché de módulos los imports DIRECTOS del
#      módulo dinámico, pero al importarlo pide la cadena TRANSITIVA
#      (modeling_lora → mha → xlm_padding...). El caché se hornea YA POBLADO
#      en la imagen: hf-modules/ → /app/.cache/huggingface/modules (COPY del
#      Dockerfile.infinity).
#   4. AutoProcessor.save anida image_processor en processor_config.json y
#      revienta la carga del TinyCLIP: se quita la clave.
#
# Regla de oro de las descargas: HF sirve los 404 como TEXTO plano con HTTP 200
# ("Entry not found") — cada fichero se valida como Python real.

import argparse
import json
import shutil
import sys
from pathlib import Path

import requests

# --- rutas del contexto de build (relativas a la raíz del repo) ---
JINA_KIT = Path("models/jina-clip-v2")
TEXT_TOWER = JINA_KIT / "text_tower"
TINYCLIP_KIT = Path("models/tinyclip")
HF_MODULES = Path("hf-modules/transformers_modules")

# --- rutas de RUNTIME (dentro del contenedor; las referencias horneadas apuntan aquí) ---
RUNTIME_KIT = "/data/clip_model"
RUNTIME_TEXT_TOWER = f"{RUNTIME_KIT}/text_tower"
# OJO: los subdirectorios del caché de módulos se derivan del BASENAME del path
# de carga (clip_model, text_tower). Si cambia RUNTIME_KIT, cambian con él.

# --- fuentes en el Hub ---
JINA_IMPL_REPO = "jinaai/jina-clip-implementation"
JINA_IMPL_FILES = [
    "configuration_clip.py", "eva_model.py", "hf_model.py", "modeling_clip.py",
    "processing_clip.py", "rope_embeddings.py", "transform.py",
]
XLMR_IMPL_REPO = "jinaai/xlm-roberta-flash-implementation"
XLMR_IMPL_FILES = [
    "block.py", "configuration_xlm_roberta.py", "embedding.py", "mha.py", "mlp.py",
    "modeling_lora.py", "modeling_xlm_roberta.py", "rotary.py",
    "stochastic_depth.py", "xlm_padding.py",
]
TEXT_TOWER_CFG_SOURCE = "jinaai/jina-embeddings-v3"
TOKENIZER_FILES = ["tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"]


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def fetch_raw_py(repo, filename):
    """Descarga un .py del Hub y valida que es Python de verdad (anti-404 textual)."""
    url = f"https://huggingface.co/{repo}/raw/main/{filename}"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    text = r.text
    head = text.lstrip()[:64]
    if head.startswith(("Entry not found", "<!DOCTYPE", '{"error')) or len(text) < 200:
        fail(f"descarga inválida para {repo}/{filename}: ¿fichero renombrado/eliminado upstream?")
    if not any(k in text for k in ("import ", "class ", "def ")):
        fail(f"{repo}/{filename} no parece Python (sin import/class/def)")
    return text


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2))


def relativize_auto_map(cfg_path):
    """auto_map de forma de hub (repo--fichero.Clase) → relativa (fichero.Clase)."""
    d = read_json(cfg_path)
    am = d.get("auto_map")
    if not am:
        print(f"ℹ️ {cfg_path}: sin auto_map — nada que relativizar")
        return
    d["auto_map"] = {k: str(v).split("--")[-1] for k, v in am.items()}
    write_json(cfg_path, d)
    print(f"🧭 auto_map relativo en {cfg_path}: {d['auto_map']}")


def fix_tinyclip():
    """(4) AutoProcessor.save anida image_processor → crash en carga. Quitar la clave."""
    p = TINYCLIP_KIT / "processor_config.json"
    if not p.exists():
        return
    d = read_json(p)
    if "image_processor" in d:
        d.pop("image_processor")
        write_json(p, d)
        print("🩹 TinyCLIP: image_processor anidado eliminado de processor_config.json")


def surgery_done():
    return (TEXT_TOWER / "modeling_lora.py").exists()


def build_jina_kit():
    # (1) .py de la implementación dentro del kit
    for f in JINA_IMPL_FILES:
        (JINA_KIT / f).write_text(fetch_raw_py(JINA_IMPL_REPO, f))
        print(f"📥 {JINA_IMPL_REPO}/{f} → {JINA_KIT / f}")

    # (2) text tower horneado: config + .py + tokenizer
    from transformers import AutoConfig
    cfg = AutoConfig.from_pretrained(TEXT_TOWER_CFG_SOURCE, trust_remote_code=True)
    TEXT_TOWER.mkdir(parents=True, exist_ok=True)
    cfg.save_pretrained(str(TEXT_TOWER))
    for f in XLMR_IMPL_FILES:
        (TEXT_TOWER / f).write_text(fetch_raw_py(XLMR_IMPL_REPO, f))
        print(f"📥 {XLMR_IMPL_REPO}/{f} → {TEXT_TOWER / f}")
    for f in TOKENIZER_FILES:
        src = JINA_KIT / f
        if not src.exists():
            fail(f"falta {src}: el kit debe venir hidratado por prepare_model.py (sección 3)")
        shutil.copy2(src, TEXT_TOWER / f)

    tt_cfg = read_json(TEXT_TOWER / "config.json")
    tt_cfg["_name_or_path"] = RUNTIME_TEXT_TOWER
    write_json(TEXT_TOWER / "config.json", tt_cfg)
    relativize_auto_map(TEXT_TOWER / "config.json")

    # (2b) el kit apunta su text tower al path de runtime horneado
    kit_cfg_path = JINA_KIT / "config.json"
    kit_cfg = read_json(kit_cfg_path)
    if "text_config" not in kit_cfg:
        fail("config.json del kit sin text_config — ¿kit hidratado correctamente?")
    kit_cfg["text_config"]["hf_model_name_or_path"] = RUNTIME_TEXT_TOWER
    write_json(kit_cfg_path, kit_cfg)
    print(f"🧭 text tower del kit → {RUNTIME_TEXT_TOWER}")

    # (1b) auto_map relativo en los configs del kit
    relativize_auto_map(kit_cfg_path)
    relativize_auto_map(JINA_KIT / "preprocessor_config.json")


def build_hf_modules():
    """(3) caché de módulos pre-poblado para el COPY del Dockerfile. Siempre se regenera."""
    if HF_MODULES.exists():
        shutil.rmtree(HF_MODULES)
    clip_cache = HF_MODULES / Path(RUNTIME_KIT).name
    tt_cache = HF_MODULES / Path(RUNTIME_TEXT_TOWER).name
    clip_cache.mkdir(parents=True)
    tt_cache.mkdir(parents=True)
    for f in JINA_IMPL_FILES:
        shutil.copy2(JINA_KIT / f, clip_cache / f)
    for f in XLMR_IMPL_FILES:
        shutil.copy2(TEXT_TOWER / f, tt_cache / f)
    print(f"📦 hf-modules regenerado: {len(JINA_IMPL_FILES)}+{len(XLMR_IMPL_FILES)} módulos en {HF_MODULES}/")


def sanity():
    kit_cfg = read_json(JINA_KIT / "config.json")
    checks = [
        ((JINA_KIT / "modeling_clip.py").exists(), "kit: modeling_clip.py en su sitio"),
        ((kit_cfg.get("auto_map") or {}).get("AutoModel") == "modeling_clip.JinaCLIPModel",
         "kit: auto_map relativo (AutoModel → modeling_clip.JinaCLIPModel)"),
        ((kit_cfg.get("text_config", {}).get("hf_model_name_or_path") == RUNTIME_TEXT_TOWER),
         f"kit: text tower → {RUNTIME_TEXT_TOWER}"),
        ((TEXT_TOWER / "tokenizer.json").exists(), "text tower: tokenizer.json"),
        ((read_json(TEXT_TOWER / "config.json").get("_name_or_path") == RUNTIME_TEXT_TOWER),
         "text tower: _name_or_path local (no apunta al Hub)"),
        ((HF_MODULES / Path(RUNTIME_KIT).name / "modeling_clip.py").exists(),
         "hf-modules: clip_model/modeling_clip.py"),
        ((HF_MODULES / Path(RUNTIME_TEXT_TOWER).name / "xlm_padding.py").exists(),
         "hf-modules: text_tower/xlm_padding.py (el import transitivo que nos costó)"),
    ]
    ok = True
    for passed, label in checks:
        print(f"{'✅' if passed else '❌'} {label}")
        ok = ok and passed
    return ok


def main():
    ap = argparse.ArgumentParser(description="Cirugía del kit CLIP jina-clip-v2 para offline total.")
    ap.add_argument("--force", action="store_true", help="rehacer la cirugía aunque ya esté hecha")
    args = ap.parse_args()

    if not JINA_KIT.exists():
        fail(f"no existe {JINA_KIT}: ejecuta antes prepare_model.py (hidratación online)")

    if surgery_done() and not args.force:
        print("⏭️ Kit jina ya operado (text_tower/modeling_lora.py presente) — salteando cirugía (--force para rehacer).")
    else:
        print("🚀 Cirugía del kit jina (auto_map relativo + text tower + referencias runtime)...")
        build_jina_kit()

    fix_tinyclip()
    build_hf_modules()

    print("\nSanity del kit self-contained:")
    if not sanity():
        fail("sanity incompleto — revisa los ❌ de arriba")
    print("\n✅ Kit CLIP self-contained listo. El Dockerfile hornea hf-modules/ junto a los modelos.")


if __name__ == "__main__":
    main()
