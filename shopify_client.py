"""
Small wrapper around the Shopify Admin GraphQL API.

It can:
  - get an access token (either a fixed shpat_ token, or a 24h token from
    the app's Client ID + Secret using Shopify's "client credentials grant")
  - fetch every product in the store (handles pagination)
  - update a product's description and SEO meta title / meta description
"""
import time

import requests

import config
import shopify_oauth
import stores


class ShopifyError(Exception):
    pass


class ShopifyClient:
    def __init__(self, shop: str | None = None):
        """shop = a myshopify domain; by default the store currently worked on
        (the main store from .env, or a connected client store - Version 3)."""
        self.shop = shop or stores.current()
        if not self.shop:
            raise ShopifyError("SHOPIFY_STORE_DOMAIN is missing in .env")
        self.graphql_url = (
            f"https://{self.shop}/admin/api/{config.SHOPIFY_API_VERSION}/graphql.json"
        )

        # Client store: its token comes from stores.json (connected on the Stores page)
        self.client_store = stores.get(self.shop)
        if self.client_store is None and self.shop != config.SHOPIFY_STORE_DOMAIN:
            raise ShopifyError(f"{self.shop} isn't connected. Add it on the Stores page.")
        if self.client_store is not None:
            self._token = None
            self._token_expires_at = 0.0
            return

        # A fixed token never expires, so give it an "infinite" expiry time.
        self._token = config.SHOPIFY_ACCESS_TOKEN or None
        self._token_expires_at = float("inf") if self._token else 0.0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def _get_token(self) -> str:
        """Return a valid access token, fetching a new one if needed."""
        if self.client_store is not None:
            return shopify_oauth.access_token(self.shop)
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        if not (config.SHOPIFY_CLIENT_ID and config.SHOPIFY_CLIENT_SECRET):
            raise ShopifyError(
                "No Shopify credentials. Set SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET "
                "(or SHOPIFY_ACCESS_TOKEN) in .env"
            )

        # Client credentials grant: swap Client ID + Secret for a ~24h token.
        resp = requests.post(
            f"https://{self.shop}/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": config.SHOPIFY_CLIENT_ID,
                "client_secret": config.SHOPIFY_CLIENT_SECRET,
            },
            timeout=30,
        )
        if not resp.ok:
            raise ShopifyError(
                f"Could not get a Shopify access token ({resp.status_code}): {resp.text[:300]}\n"
                "Check that the app is released with read_products/write_products scopes, "
                "installed on this store, and that the store was created from the Dev Dashboard."
            )

        data = resp.json()
        self._token = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 86399))
        return self._token

    # ------------------------------------------------------------------
    # Low-level GraphQL call (with simple retry when Shopify throttles us)
    # ------------------------------------------------------------------
    def graphql(self, query: str, variables: dict | None = None) -> dict:
        for attempt in range(5):
            resp = requests.post(
                self.graphql_url,
                json={"query": query, "variables": variables or {}},
                headers={
                    "X-Shopify-Access-Token": self._get_token(),
                    "Content-Type": "application/json",
                },
                timeout=60,
            )

            # HTTP-level rate limit -> wait and try again
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if not resp.ok:
                raise ShopifyError(f"Shopify API error {resp.status_code}: {resp.text[:300]}")

            body = resp.json()
            errors = body.get("errors")
            if errors:
                # GraphQL cost limit -> wait and try again
                if any(e.get("extensions", {}).get("code") == "THROTTLED" for e in errors):
                    time.sleep(2 * (attempt + 1))
                    continue
                raise ShopifyError(f"Shopify GraphQL error: {errors}")

            return body["data"]

        raise ShopifyError("Shopify kept rate-limiting the request; try again later.")

    # ------------------------------------------------------------------
    # High-level helpers
    # ------------------------------------------------------------------
    def get_shop_name(self) -> str:
        """Simple call used to test the connection."""
        return self.graphql("{ shop { name } }")["shop"]["name"]

    def _fetch_all(self, query: str, root: str) -> list[dict]:
        """Run a paginated query (50 items per page) and return all nodes."""
        items, cursor = [], None
        while True:
            data = self.graphql(query, {"cursor": cursor})[root]
            items.extend(data["nodes"])
            if not data["pageInfo"]["hasNextPage"]:
                return items
            cursor = data["pageInfo"]["endCursor"]

    @staticmethod
    def _seo_input(item_id: str, description_html: str, meta_title: str, meta_description: str) -> dict:
        """Build {id, descriptionHtml, seo}. Empty values are skipped,
        so we never wipe existing content by accident."""
        data: dict = {"id": item_id}
        if description_html.strip():
            data["descriptionHtml"] = description_html
        seo = {}
        if meta_title.strip():
            seo["title"] = meta_title.strip()
        if meta_description.strip():
            seo["description"] = meta_description.strip()
        if seo:
            data["seo"] = seo
        return data

    @staticmethod
    def _check_user_errors(result: dict) -> None:
        if result["userErrors"]:
            raise ShopifyError(f"Shopify rejected the update: {result['userErrors']}")

    # ---------- products (Version 1) ----------
    def fetch_all_products(self) -> list[dict]:
        """Return every product in the store as a list of plain dicts."""
        query = """
        query Products($cursor: String) {
          products(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              title
              handle
              status
              vendor
              productType
              tags
              descriptionHtml
              seo { title description }
            }
          }
        }
        """
        return self._fetch_all(query, "products")

    def update_product(
        self, product_id: str, description_html: str, meta_title: str, meta_description: str
    ) -> None:
        """Write the new description + SEO fields to one product."""
        mutation = """
        mutation UpdateProductSeo($product: ProductUpdateInput!) {
          productUpdate(product: $product) {
            product { id }
            userErrors { field message }
          }
        }
        """
        product = self._seo_input(product_id, description_html, meta_title, meta_description)
        self._check_user_errors(self.graphql(mutation, {"product": product})["productUpdate"])

    # ---------- collections (Version 2) ----------
    def fetch_all_collections(self) -> list[dict]:
        """Every collection, with the first 10 product titles (used as facts for the AI)."""
        query = """
        query Collections($cursor: String) {
          collections(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              title
              handle
              descriptionHtml
              seo { title description }
              productsCount { count }
              products(first: 10) { nodes { title } }
            }
          }
        }
        """
        return self._fetch_all(query, "collections")

    def update_collection(
        self, collection_id: str, description_html: str, meta_title: str, meta_description: str
    ) -> None:
        mutation = """
        mutation UpdateCollectionSeo($collection: CollectionUpdateInput!) {
          collectionUpdate(collection: $collection) {
            collection { id }
            userErrors { field message }
          }
        }
        """
        collection = self._seo_input(collection_id, description_html, meta_title, meta_description)
        self._check_user_errors(
            self.graphql(mutation, {"collection": collection})["collectionUpdate"]
        )

    # ---------- SEO audit (Version 3) ----------
    def fetch_products_for_audit(self) -> list[dict]:
        """Products with their SEO fields and the alt text of every photo."""
        query = """
        query AuditProducts($cursor: String) {
          products(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              title
              handle
              status
              descriptionHtml
              seo { title description }
              media(first: 20) { nodes { alt mediaContentType } }
            }
          }
        }
        """
        return self._fetch_all(query, "products")

    # ---------- undo (Version 3) ----------
    def restore_seo(self, table: str, item_id: str, backup: dict) -> None:
        """Put a backup back on a product or collection. Unlike the normal update,
        empty values ARE sent (the original really was empty). Fields missing
        from the backup are left alone."""
        data: dict = {"id": item_id}
        if "description_html" in backup:
            data["descriptionHtml"] = backup["description_html"]
        seo = {name: backup[f"meta_{name}"] for name in ("title", "description")
               if f"meta_{name}" in backup}
        if seo:
            data["seo"] = seo
        if len(data) == 1:
            return  # nothing known to restore
        if table == "products":
            mutation = """
            mutation RestoreProduct($input: ProductUpdateInput!) {
              productUpdate(product: $input) { product { id } userErrors { field message } }
            }"""
            self._check_user_errors(self.graphql(mutation, {"input": data})["productUpdate"])
        else:
            mutation = """
            mutation RestoreCollection($input: CollectionUpdateInput!) {
              collectionUpdate(collection: $input) { collection { id } userErrors { field message } }
            }"""
            self._check_user_errors(self.graphql(mutation, {"input": data})["collectionUpdate"])

    # ---------- product images / alt text (Version 2) ----------
    def fetch_all_product_images(self) -> list[dict]:
        """One dict per product image: media_id, product_id, product_title, image_url, alt."""
        query = """
        query ProductImages($cursor: String) {
          products(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id
              title
              media(first: 20) {
                nodes {
                  id
                  alt
                  mediaContentType
                  ... on MediaImage { image { url } }
                }
              }
            }
          }
        }
        """
        images = []
        for product in self._fetch_all(query, "products"):
            for media in product["media"]["nodes"]:
                if media["mediaContentType"] != "IMAGE" or not media.get("image"):
                    continue  # skip videos, 3D models, images still processing
                images.append({
                    "media_id": media["id"],
                    "product_id": product["id"],
                    "product_title": product["title"],
                    "image_url": media["image"]["url"],
                    "alt": " ".join((media.get("alt") or "").split()),
                })
        return images

    def update_image_alt(self, media_id: str, alt: str) -> None:
        """Set the alt text of one product image. Needs the write_files scope."""
        mutation = """
        mutation UpdateImageAlt($files: [FileUpdateInput!]!) {
          fileUpdate(files: $files) {
            files { id alt }
            userErrors { field message }
          }
        }
        """
        try:
            result = self.graphql(mutation, {"files": [{"id": media_id, "alt": (alt or "").strip()}]})
        except ShopifyError as e:
            if "access" in str(e).lower():
                raise ShopifyError(
                    "Shopify says the app may not change images. Add the scopes "
                    "read_files,write_files to the app in the Dev Dashboard, release a new "
                    "version and approve it on the store."
                ) from e
            raise
        self._check_user_errors(result["fileUpdate"])
