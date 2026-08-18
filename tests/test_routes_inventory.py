"""Inventory endpoints, with focus on the multipart create path.

POST /inventory takes multipart/form-data, which cannot use a Pydantic model —
its bounds are re-asserted by hand in the handler. That hand-written validation
is exactly the kind that drifts out of sync with models.py, so it is tested here
directly rather than through the model.
"""

import io
from unittest import mock

import pytest
from PIL import Image

UID = "test-uid"
STOCK = f"users/{UID}/stock"


def make_photo(fmt="JPEG", size=(600, 400)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 140, 200)).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def fake_bucket():
    """Storage double. Uploaded blobs land in `bucket.blobs` keyed by path."""
    bucket = mock.MagicMock()
    bucket.name = "test-bucket.appspot.com"
    bucket.blobs = {}

    def _blob(path):
        blob = mock.MagicMock()
        blob.path = path
        blob.upload_from_string.side_effect = lambda data, **kw: bucket.blobs.__setitem__(
            path, data
        )
        return blob

    bucket.blob.side_effect = _blob
    with mock.patch("routes.inventory.get_bucket", return_value=bucket):
        yield bucket


@pytest.fixture
def direct_transactions():
    """`_create` is decorated per-request, so patching the decorator works.

    The real firestore.transactional drives a live backend session; the fake
    Firestore has no such session.
    """
    with mock.patch("routes.inventory.firestore.transactional", lambda f: f):
        yield


@pytest.fixture
def create_item(authed_client, direct_transactions):
    def _create(**overrides):
        data = {"item": "rice", "price": "50"}
        files = overrides.pop("files", None)
        data.update({k: str(v) for k, v in overrides.items()})
        return authed_client.post("/inventory", data=data, files=files)

    return _create


class TestGetInventory:
    def test_empty(self, authed_client, fake_db):
        assert authed_client.get("/inventory").json() == {"inventory": []}

    def test_returns_seeded_items(self, authed_client, fake_db):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 10, "price": 50})
        body = authed_client.get("/inventory").json()
        assert len(body["inventory"]) == 1
        assert body["inventory"][0]["item"] == "rice"

    def test_scoped_to_the_caller(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/stock/rice", {"item": "rice", "quantity": 99})
        assert authed_client.get("/inventory").json()["inventory"] == []


class TestCreateItemValidation:
    def test_creates_a_stock_document(self, fake_db, create_item):
        resp = create_item(item="  Basmati Rice  ", price=120, quantity=8)

        assert resp.status_code == 200, resp.text
        doc = fake_db.docs[f"{STOCK}/basmati rice"]
        assert (doc["quantity"], doc["price"]) == (8, 120)

    def test_quantity_defaults_to_zero(self, fake_db, create_item):
        create_item(item="rice", price=50)
        assert fake_db.docs[f"{STOCK}/rice"]["quantity"] == 0

    @pytest.mark.parametrize("name", ["   ", "a" * 101])
    def test_bad_item_name_length_is_400(self, fake_db, create_item, name):
        assert create_item(item=name).status_code == 400

    def test_absent_item_name_is_422(self, fake_db, create_item):
        """An empty form value is dropped on the wire, so `item` arrives missing
        and FastAPI's required-field check rejects it before the handler runs."""
        assert create_item(item="").status_code == 422

    @pytest.mark.parametrize(
        "name",
        [
            "rice/basmati",  # '/' would create a nested path
            ".",
            "..",
            "__proto__",  # matches Firestore's reserved __.*__ pattern
            "rice\x00null",
        ],
    )
    def test_item_names_that_are_illegal_firestore_ids_are_400(self, fake_db, create_item, name):
        resp = create_item(item=name)
        assert resp.status_code == 400, f"{name!r} should be rejected, not written as a doc id"
        assert fake_db.paths_under(STOCK) == []

    @pytest.mark.parametrize("price", [-1, 10_000_001])
    def test_price_out_of_range_is_400(self, fake_db, create_item, price):
        assert create_item(price=price).status_code == 400

    @pytest.mark.parametrize("quantity", [-1, 100_001])
    def test_quantity_out_of_range_is_400(self, fake_db, create_item, quantity):
        assert create_item(quantity=quantity).status_code == 400

    def test_cost_price_out_of_range_is_400(self, fake_db, create_item):
        assert create_item(cost_price=10_000_001).status_code == 400

    @pytest.mark.parametrize("unit", ["", "pcs", "dozen", "box", "pack", "PCS", " Dozen "])
    def test_allowed_units_are_accepted(self, fake_db, create_item, unit):
        assert (
            create_item(item=f"item-{unit.strip().lower() or 'blank'}", unit=unit).status_code
            == 200
        )

    @pytest.mark.parametrize("unit", ["kg", "litre", "gram", "nonsense"])
    def test_disallowed_units_are_400(self, fake_db, create_item, unit):
        assert create_item(unit=unit).status_code == 400

    def test_category_is_truncated_not_rejected(self, fake_db, create_item):
        create_item(category="c" * 200)
        assert len(fake_db.docs[f"{STOCK}/rice"]["category"]) == 50

    def test_duplicate_item_is_409(self, fake_db, create_item):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 25, "price": 40})

        resp = create_item(item="rice", price=99)

        assert resp.status_code == 409
        assert fake_db.docs[f"{STOCK}/rice"]["quantity"] == 25, "existing stock was clobbered"

    def test_another_users_item_is_not_a_duplicate(self, fake_db, create_item):
        fake_db.seed("users/someone-else/stock/rice", {"item": "rice", "quantity": 5})
        assert create_item(item="rice").status_code == 200


class TestCreateItemWithPhoto:
    def test_uploads_main_and_thumbnail_as_webp(self, fake_db, create_item, fake_bucket):
        resp = create_item(files={"image": ("photo.jpg", make_photo(), "image/jpeg")})

        assert resp.status_code == 200, resp.text
        assert len(fake_bucket.blobs) == 2, "expected a main image and a thumbnail"
        for data in fake_bucket.blobs.values():
            assert Image.open(io.BytesIO(data)).format == "WEBP"

    def test_blob_paths_are_uuid_keyed_not_item_name_keyed(self, fake_db, create_item, fake_bucket):
        """A rename would orphan a name-keyed blob."""
        create_item(item="basmati rice", files={"image": ("p.jpg", make_photo(), "image/jpeg")})

        for path in fake_bucket.blobs:
            assert path.startswith(f"users/{UID}/items/")
            assert "basmati" not in path

    def test_response_carries_image_urls(self, fake_db, create_item, fake_bucket):
        body = create_item(files={"image": ("p.jpg", make_photo(), "image/jpeg")}).json()
        assert body["image_url"].startswith("https://")
        assert body["thumb_url"]

    def test_rejected_image_is_400_and_writes_nothing(self, fake_db, create_item, fake_bucket):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

        resp = create_item(files={"image": ("evil.svg", svg, "image/svg+xml")})

        assert resp.status_code == 400
        assert fake_bucket.blobs == {}, "a rejected upload must not reach storage"
        assert fake_db.paths_under(STOCK) == []

    def test_content_type_cannot_launder_a_bad_file(self, fake_db, create_item, fake_bucket):
        """The declared content type is attacker-controlled and must be ignored."""
        resp = create_item(files={"image": ("photo.jpg", b"GIF89a" + b"\x00" * 64, "image/jpeg")})
        assert resp.status_code == 400

    def test_oversized_upload_is_rejected(self, fake_db, create_item, fake_bucket):
        oversized = b"\xff\xd8\xff" + b"\x00" * (4 * 1024 * 1024)
        resp = create_item(files={"image": ("big.jpg", oversized, "image/jpeg")})

        assert resp.status_code in (400, 413), resp.text
        assert fake_bucket.blobs == {}

    def test_photoless_create_never_touches_storage(self, fake_db, create_item, fake_bucket):
        create_item()
        assert fake_bucket.blobs == {}
        fake_bucket.blob.assert_not_called()


class TestUpdateAndDelete:
    """PUT is multipart, not JSON, so the edit sheet can attach a photo the same
    way the add sheet does — hence `data=`/`files=` throughout."""

    def test_update_quantity(self, authed_client, fake_db):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 10, "price": 50})

        resp = authed_client.put("/inventory/rice", data={"quantity": 25})

        assert resp.status_code == 200
        assert fake_db.docs[f"{STOCK}/rice"]["quantity"] == 25

    def test_update_unknown_item_is_404(self, authed_client, fake_db):
        assert authed_client.put("/inventory/nope", data={"quantity": 1}).status_code == 404

    def test_cannot_update_another_users_item(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/stock/rice", {"item": "rice", "quantity": 10})

        assert authed_client.put("/inventory/rice", data={"quantity": 999}).status_code == 404
        assert fake_db.docs["users/someone-else/stock/rice"]["quantity"] == 10

    def test_negative_quantity_is_422(self, authed_client, fake_db):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 10})
        assert authed_client.put("/inventory/rice", data={"quantity": -5}).status_code == 422

    def test_photoless_update_never_touches_storage(self, authed_client, fake_db, fake_bucket):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 10})

        authed_client.put("/inventory/rice", data={"quantity": 11})

        fake_bucket.blob.assert_not_called()

    def test_adding_a_photo_stores_both_sizes(self, authed_client, fake_db, fake_bucket):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 10})

        resp = authed_client.put(
            "/inventory/rice", files={"image": ("p.jpg", make_photo(), "image/jpeg")}
        )

        assert resp.status_code == 200, resp.text
        doc = fake_db.docs[f"{STOCK}/rice"]
        assert len(fake_bucket.blobs) == 2
        assert set(fake_bucket.blobs) == {doc["image_path"], doc["image_thumb_path"]}
        assert resp.json()["image_url"].startswith("https://firebasestorage.googleapis.com/")

    def test_replacing_a_photo_writes_a_new_path_and_drops_the_old(
        self, authed_client, fake_db, fake_bucket
    ):
        fake_db.seed(
            f"{STOCK}/rice",
            {
                "item": "rice",
                "image_id": "old",
                "image_token": "tok",
                "image_path": f"users/{UID}/items/old.webp",
                "image_thumb_path": f"users/{UID}/items/old_thumb.webp",
            },
        )

        resp = authed_client.put(
            "/inventory/rice", files={"image": ("p.jpg", make_photo(), "image/jpeg")}
        )

        assert resp.status_code == 200, resp.text
        doc = fake_db.docs[f"{STOCK}/rice"]
        # A fresh uuid path, never a rewrite of the old one — the uploaded bytes
        # are cached immutably for a year.
        assert doc["image_id"] != "old"
        assert "old.webp" not in doc["image_path"]
        deleted = [c.args[0] for c in fake_bucket.blob.call_args_list]
        assert f"users/{UID}/items/old.webp" in deleted

    def test_remove_image_clears_the_fields(self, authed_client, fake_db, fake_bucket):
        fake_db.seed(
            f"{STOCK}/rice",
            {
                "item": "rice",
                "image_id": "old",
                "image_token": "tok",
                "image_path": f"users/{UID}/items/old.webp",
                "image_thumb_path": f"users/{UID}/items/old_thumb.webp",
            },
        )

        resp = authed_client.put("/inventory/rice", data={"remove_image": "true"})

        assert resp.status_code == 200, resp.text
        doc = fake_db.docs[f"{STOCK}/rice"]
        assert not doc["image_path"] and not doc["image_thumb_path"]
        assert authed_client.get("/inventory").json()["inventory"][0].get("thumb_url") is None

    def test_rename_carries_the_photo_over(self, authed_client, fake_db):
        fake_db.seed(
            f"{STOCK}/rice",
            {
                "item": "rice",
                "quantity": 4,
                "image_id": "old",
                "image_token": "tok",
                "image_path": f"users/{UID}/items/old.webp",
                "image_thumb_path": f"users/{UID}/items/old_thumb.webp",
            },
        )

        resp = authed_client.put("/inventory/rice", data={"item": "basmati rice"})

        assert resp.status_code == 200, resp.text
        assert f"{STOCK}/rice" not in fake_db.docs
        assert fake_db.docs[f"{STOCK}/basmati rice"]["image_path"].endswith("old.webp")

    def test_rename_with_a_new_photo_keeps_the_new_one(self, authed_client, fake_db, fake_bucket):
        fake_db.seed(
            f"{STOCK}/rice",
            {
                "item": "rice",
                "image_id": "old",
                "image_token": "tok",
                "image_path": f"users/{UID}/items/old.webp",
                "image_thumb_path": f"users/{UID}/items/old_thumb.webp",
            },
        )

        resp = authed_client.put(
            "/inventory/rice",
            data={"item": "basmati rice"},
            files={"image": ("p.jpg", make_photo(), "image/jpeg")},
        )

        assert resp.status_code == 200, resp.text
        moved = fake_db.docs[f"{STOCK}/basmati rice"]
        assert moved["image_id"] != "old"
        assert moved["image_path"] in fake_bucket.blobs

    def test_rename_onto_an_existing_name_uploads_nothing(
        self, authed_client, fake_db, fake_bucket
    ):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice"})
        fake_db.seed(f"{STOCK}/dal", {"item": "dal"})

        resp = authed_client.put(
            "/inventory/rice",
            data={"item": "dal"},
            files={"image": ("p.jpg", make_photo(), "image/jpeg")},
        )

        assert resp.status_code == 400
        assert fake_bucket.blobs == {}

    def test_delete_removes_the_item(self, authed_client, fake_db):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 10})

        assert authed_client.delete("/inventory/rice").status_code == 200
        assert f"{STOCK}/rice" not in fake_db.docs

    def test_cannot_delete_another_users_item(self, authed_client, fake_db):
        fake_db.seed("users/someone-else/stock/rice", {"item": "rice", "quantity": 10})

        authed_client.delete("/inventory/rice")

        assert "users/someone-else/stock/rice" in fake_db.docs


class TestConfirmClearInventory:
    def test_clears_only_the_callers_stock(self, authed_client, fake_db):
        fake_db.seed(f"{STOCK}/rice", {"item": "rice", "quantity": 10})
        fake_db.seed(f"{STOCK}/dal", {"item": "dal", "quantity": 5})
        fake_db.seed("users/someone-else/stock/rice", {"item": "rice", "quantity": 99})

        assert authed_client.post("/confirm_clear_inventory").status_code == 200

        assert fake_db.paths_under(STOCK) == []
        assert "users/someone-else/stock/rice" in fake_db.docs
