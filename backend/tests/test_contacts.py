def test_contact_crud(client):
    created = client.post(
        "/api/contacts", json={"name": "Alice", "phone": "+447700900001", "notes": "vip"}
    )
    assert created.status_code == 201
    cid = created.json()["id"]

    assert client.get("/api/contacts").json()[0]["name"] == "Alice"
    assert client.get(f"/api/contacts/{cid}").json()["phone"] == "+447700900001"

    dup = client.post("/api/contacts", json={"name": "Bob", "phone": "+447700900001"})
    assert dup.status_code == 409

    patched = client.patch(f"/api/contacts/{cid}", json={"name": "Alice B"})
    assert patched.json()["name"] == "Alice B"

    assert client.delete(f"/api/contacts/{cid}").status_code == 204
    assert client.get(f"/api/contacts/{cid}").status_code == 404


def test_contact_search(client):
    client.post("/api/contacts", json={"name": "Zed", "phone": "+15550000001"})
    client.post("/api/contacts", json={"name": "Yan", "phone": "+15550000002"})
    results = client.get("/api/contacts", params={"search": "zed"}).json()
    assert len(results) == 1
    assert results[0]["name"] == "Zed"


def test_csv_import(client):
    csv_body = "name,phone,notes\nAmy,+15551110001,\nBen,+15551110002,cold lead\n,missing,\n"
    resp = client.post(
        "/api/contacts/import",
        files={"file": ("contacts.csv", csv_body, "text/csv")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    assert len(data["errors"]) == 1

    again = client.post(
        "/api/contacts/import",
        files={"file": ("contacts.csv", csv_body, "text/csv")},
    )
    assert again.json()["skipped"] == 2
