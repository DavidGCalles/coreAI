import os
import logging
import time
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient, models
from src.schemas.memory import EpisodicMemoryItem, EpisodicMemoryMetadata
from src.managers.config_manager import config_manager

from src.logging_config import configure_logging
configure_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
# 384 for intfloat/multilingual-e5-small (local)
# 768 for text-embedding-004 (Gemini)
EMBEDDING_DIMENSION = 384

class VectorMemoryManager:
    """
    Repository layer for semantic memory stored in Qdrant.

    After ADR-012 we no longer have a single "catch all" collection.  The
    vector store is split into three semantic domains:

        * mem_episodic   (chat/working memory)
        * mem_documents  (ingested documents)
        * mem_system     (system or agent knowledge)

    A manager instance is bound to one of the domains and will automatically
    create the other two collections at startup so that the database is
    always in a known state.  The constructor accepts either a domain enum
    value or the raw collection name for backwards compatibility.
    """

    # mapping used by the constructor and initialization helpers
    DOMAIN_COLLECTION_MAP = {
        "episodic": "mem_episodic",
        "document": "mem_documents",
        "system": "mem_system",
    }

    def __init__(self, domain: str | None = None, collection_name: str | None = None):
        # the caller may provide either a high‑level domain or a specific
        # collection name.  domain takes precedence when supplied.
        if domain is not None:
            # support enum instances or raw strings
            if hasattr(domain, "value"):
                domain_key = domain.value
            else:
                domain_key = str(domain)
            domain_key = domain_key.lower()
            if domain_key not in self.DOMAIN_COLLECTION_MAP:
                raise ValueError(f"Unknown memory domain: {domain}")
            self._collection_name = self.DOMAIN_COLLECTION_MAP[domain_key]
        elif collection_name is not None:
            self._collection_name = collection_name
        else:
            # default to episodic for backwards compatibility
            self._collection_name = self.DOMAIN_COLLECTION_MAP["episodic"]

        self._client = None

    async def _initialize_client(self):
        """
        Connects to Qdrant and then bootstraps the three required collections.
        This is intentionally lazy so that tests can inject a fake client before
        any network activity occurs.
        """
        if self._client is None:
            q_config = config_manager.get_qdrant_config()
            host = q_config["url"]
            api_key = q_config["api_key"]

            logger.info("🔌 Connecting to Memory Store...")

            try:
                if api_key:
                    logger.info(f"   -> Mode: Cloud, URL: {host}")
                    self._client = AsyncQdrantClient(url=host, api_key=api_key)
                else:
                    logger.info(f"   -> Mode: Docker, Host: {host}")
                    # Assume HTTP connection for local instances
                    url = host if "://" in host else f"http://{host}:6333"
                    self._client = AsyncQdrantClient(url=url)
            except Exception as e:
                logger.error(f"Failed to connect to Qdrant: {e}", exc_info=True)
                raise ConnectionError(f"Failed to connect to Qdrant: {e}")

            # ensure that every domain collection exists before we use them
            await self._ensure_all_collections()

    async def _ensure_collection(self, collection_name: str) -> None:
        """
        Ensure a single named collection exists (create if missing) and has the
        required payload indexes.  This helper is reused when bootstrapping all
        three domain collections.
        """
        try:
            await self._client.get_collection(collection_name=collection_name)
        except Exception:
            logger.info(f"Collection '{collection_name}' not found. Creating a new one...")
            await self._client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info(f"✅ Collection '{collection_name}' created successfully.")

        # create same set of payload indexes for every domain collection
        payload_indexes = [
            "domain",
            "category",
            "type",
            "source",
            "visibility",
            "owner_id",
        ]

        for field in payload_indexes:
            try:
                await self._client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                    wait=False,
                )
            except Exception as e:
                logger.debug(f"Index check for '{field}' in '{collection_name}': {e}")


    async def _ensure_all_collections(self) -> None:
        """
        On every new client initialization we guarantee that the three semantic
        collections exist.  Qdrant operations are idempotent, so it is safe to
        call this repeatedly during startup.
        """
        for coll in self.DOMAIN_COLLECTION_MAP.values():
            await self._ensure_collection(coll)

    async def _get_embedding(self, text: str) -> list[float]:
        """
        Generates an embedding for the given text using the LiteLLM proxy.
        Uses the 'text-embedding' model configured in LiteLLM which routes to the proper provider.
        """
        start_time = time.time()
        text_preview = text[:50] if isinstance(text, str) else str(text)[:50]
        logger.debug("🧵 Embedding generation starting: text_len=%d", len(text) if isinstance(text, str) else len(str(text)))
        
        litellm_url = os.getenv("LITELLM_URL", "http://localhost:4000")
        if not litellm_url.startswith("http"):
            litellm_url = f"http://{litellm_url}"
            
        client = AsyncOpenAI(
            base_url=f"{litellm_url.rstrip('/')}/v1",
            api_key="sk-fake-key"
        )
        
        try:
            response = await client.embeddings.create(
                model="text-embedding",
                input=[text]
            )
            embedding = response.data[0].embedding
            elapsed = time.time() - start_time
            logger.debug("✅ Embedding generated: dim=%d elapsed=%.2fs", len(embedding), elapsed)
            return embedding
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error("❌ Embedding generation failed after %.2fs: %s", elapsed, str(e), exc_info=True)
            raise

    async def add_memory(self, item: EpisodicMemoryItem) -> str:
        """
        Persists a strictly typed memory item into the vector store.  The
        caller is responsible for supplying a metadata object that subclasses
        `BaseMemoryMetadata`; we validate this and also check that the
        requested collection/domain matches the metadata to avoid accidental
        mis‑routing.
        """
        from src.schemas.memory import BaseMemoryMetadata

        logger.debug("💾 Adding memory item: owner=%s visibility=%s domain=%s", item.metadata.owner_id, item.metadata.visibility, item.metadata.domain)

        # validate the metadata portion against the universal contract
        metadata_dict = item.metadata.model_dump()
        # this will ensure required base fields exist and types are correct.
        # Any additional domain‑specific keys (category/type/etc.) are allowed
        # and will simply be ignored by BaseMemoryMetadata.
        try:
            BaseMemoryMetadata(**metadata_dict)
        except Exception as e:
            logger.error("❌ Memory metadata validation failed: %s", str(e), exc_info=True)
            raise

        if self._client is None:
            logger.info("🔌 Initializing Qdrant client for memory persistence...")
            await self._initialize_client()
        # just make sure our specific collection exists in case someone passed
        # a custom name via the constructor
        await self._ensure_collection(self._collection_name)

        logger.debug("🧠 Generating embedding for memory item (len=%d)", len(item.content))
        vector = await self._get_embedding(item.content)
        logger.debug("✅ Embedding generated (dim=%d)", len(vector))

        # verify domain-routing consistency (item metadata.domain -> collection)
        desired = self.DOMAIN_COLLECTION_MAP.get(str(item.metadata.domain))
        if desired and desired != self._collection_name:
            logger.warning(
                "Memory domain %s does not match manager's collection %s; "
                "routing to the metadata-defined domain instead.",
                item.metadata.domain,
                self._collection_name,
            )
            self._collection_name = desired

        try:
            logger.debug("🚀 Upserting point to collection '%s'", self._collection_name)
            await self._client.upsert(
                collection_name=self._collection_name,
                points=[
                    models.PointStruct(
                        id=item.id,
                        vector=vector,
                        payload={
                            **metadata_dict,
                            "content": item.content,
                            "created_at": item.created_at,
                            "created_by": item.created_by,
                        },
                    )
                ],
                wait=True,
            )
            logger.info("✅ Memory persisted: id=%s owner=%s collection=%s", item.id, item.metadata.owner_id, self._collection_name)
            return item.id
        except Exception as e:
            logger.error("❌ Error persisting memory %s: %s", item.id, str(e), exc_info=True)
            raise e

    async def search_memory(
        self, 
        query: str, 
        filters: dict | models.Filter | None = None, 
        limit: int = 5
    ) -> list[EpisodicMemoryItem]:
        """
        Semantic search retrieving structured objects.

        The ``filters`` argument may be supplied as a simple mapping of
        ``field_name -> value`` (which is interpreted as an **AND**/``must``
        filter) or as a ``qdrant_client.models.Filter`` instance.  The latter
        form is useful for advanced RBAC logic where ``should`` clauses are
        required; this is how :class:`MemoryGateway` enforces visibility
        rules.
        """
        logger.debug("🔎 Semantic search starting: query='%s' limit=%d collection=%s filters=%s", query[:50], limit, self._collection_name, type(filters).__name__)
        
        if self._client is None:
            await self._initialize_client()
        await self._ensure_collection(self._collection_name)
        
        logger.debug("🧠 Generating query embedding...")
        query_vector = await self._get_embedding(query)
        
        # handle the two supported filter formats
        qdrant_filter = None
        if filters is not None:
            if isinstance(filters, models.Filter):
                qdrant_filter = filters
            elif isinstance(filters, dict):
                filter_conditions = []
                for key, value in filters.items():
                    filter_conditions.append(
                        models.FieldCondition(key=key, match=models.MatchValue(value=value))
                    )
                qdrant_filter = models.Filter(must=filter_conditions) if filter_conditions else None
            else:
                # fallback: attempt to treat like dict for compatibility
                try:
                    filter_conditions = []
                    for key, value in dict(filters).items():
                        filter_conditions.append(
                            models.FieldCondition(key=key, match=models.MatchValue(value=value))
                        )
                    qdrant_filter = models.Filter(must=filter_conditions) if filter_conditions else None
                except Exception:
                    qdrant_filter = None

        try:
            logger.debug("🚀 Executing semantic query against collection '%s' with %s", self._collection_name, "filter" if qdrant_filter else "no filter")
            result_obj = await self._client.query_points(
                collection_name=self._collection_name,
                query=query_vector,
                query_filter=qdrant_filter,
                limit=limit
            )
            
            # FIX ADR-007: Ahora devuelve un objeto wrapper, extraemos la lista de puntos
            results = result_obj.points
            logger.info("✅ Semantic search completed: found %d results from '%s'", len(results), self._collection_name)
            found_items = []
            for point in results:
                metadata_payload = {k: v for k, v in point.payload.items() if k not in ["content", "created_at"]}
                metadata = EpisodicMemoryMetadata(**metadata_payload)
                
                item = EpisodicMemoryItem(
                    id=point.id,
                    content=point.payload["content"],
                    created_at=point.payload["created_at"],
                    metadata=metadata,
                    created_by=point.payload.get("created_by"),
                    score=getattr(point, "score", None)
                )
                found_items.append(item)
            
            logger.info(f"Found {len(found_items)} memories for query: '{query}'")
            return found_items

        except Exception as e:
            logger.error(f"Error searching memory for query '{query}': {e}", exc_info=True)
            return []

    async def delete_memory(self, memory_id: str):
        """
        Hard delete of a memory item by ID.
        """
        if self._client is None:
            await self._initialize_client()
        try:
            await self._client.delete(
                collection_name=self._collection_name,
                points_selector=models.PointIdsList(points=[memory_id]),
            )
            logger.info(f"🗑️ Memory {memory_id} deleted.")
        except Exception as e:
            logger.error(f"Error deleting memory {memory_id}: {e}", exc_info=True)
            raise e