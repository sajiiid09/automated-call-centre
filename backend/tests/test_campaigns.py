def _mk_contact(client, name, phone):
    return client.post("/api/contacts", json={"name": name, "phone": phone}).json()["id"]


def test_campaign_crud(client):
    c1 = _mk_contact(client, "Alice", "+15551230001")
    c2 = _mk_contact(client, "Bob", "+15551230002")

    created = client.post(
        "/api/campaigns",
        json={
            "name": "Spring promo",
            "goal": "Book demos",
            "script_prompt": "Offer the spring discount.",
            "contact_ids": [c1, c2],
        },
    )
    assert created.status_code == 201
    camp = created.json()
    assert camp["total_contacts"] == 2
    assert camp["called_contacts"] == 0
    assert camp["status"] == "draft"

    detail = client.get(f"/api/campaigns/{camp['id']}").json()
    assert {r["contact"]["name"] for r in detail["contact_rows"]} == {"Alice", "Bob"}
    assert all(r["status"] == "pending" for r in detail["contact_rows"])

    patched = client.patch(f"/api/campaigns/{camp['id']}", json={"contact_ids": [c1]})
    assert patched.json()["total_contacts"] == 1

    assert client.delete(f"/api/campaigns/{camp['id']}").status_code == 204
    assert client.get(f"/api/campaigns/{camp['id']}").status_code == 404


def test_campaign_unknown_contact(client):
    resp = client.post(
        "/api/campaigns",
        json={"name": "Bad", "contact_ids": ["00000000-0000-0000-0000-000000000001"]},
    )
    assert resp.status_code == 400
