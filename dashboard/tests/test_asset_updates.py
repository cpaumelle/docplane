"""A normal browser reload must fetch deployed bytes, even with old validators."""
import hashlib
import os
import re

from fastapi.testclient import TestClient

import dashboard.app as dashboard


def test_index_versions_local_assets_and_cannot_return_stale_html():
    client = TestClient(dashboard.app)
    response = client.get("/", headers={"If-Modified-Since": "Wed, 01 Jan 2099 00:00:00 GMT"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    expected = hashlib.sha256((dashboard._STATIC / "app.js").read_bytes()).hexdigest()[:16]
    assert f'/assets/app.js?v={expected}' in response.text
    assert re.search(r'/assets/style.css\?v=[a-f0-9]{16}', response.text)


def test_assets_return_bytes_even_when_cached_validators_match():
    client = TestClient(dashboard.app)
    first = client.get("/assets/app.js")
    second = client.get("/assets/app.js", headers={
        "If-None-Match": first.headers["etag"],
        "If-Modified-Since": first.headers["last-modified"],
    })
    assert first.status_code == second.status_code == 200
    assert second.content == (dashboard._STATIC / "app.js").read_bytes()
    assert second.headers["Cache-Control"] == "no-store"


def test_asset_url_changes_with_bytes_even_when_file_date_does_not(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text('<script src="/assets/app.js"></script>')
    asset = tmp_path / "app.js"
    asset.write_text("first build")
    monkeypatch.setattr(dashboard, "_STATIC", tmp_path)
    first = dashboard.index().body
    original_time = asset.stat().st_mtime_ns
    asset.write_text("newer build")
    os.utime(asset, ns=(original_time, original_time))
    assert dashboard.index().body != first
