def test_stats_empty(client):
    stats = client.get("/api/stats").json()
    assert stats["total_calls"] == 0
    assert stats["dispositions"] == {}


def test_stats_counts(client, db):
    from datetime import datetime, timezone

    from app.models import Call, Contact

    contact = Contact(name="S", phone="+15558880001")
    db.add(contact)
    db.flush()
    db.add_all(
        [
            Call(
                direction="inbound",
                status="completed",
                duration_seconds=60,
                disposition="interested",
                started_at=datetime.now(timezone.utc),
            ),
            Call(
                direction="outbound",
                status="completed",
                duration_seconds=120,
                disposition="callback",
                started_at=datetime.now(timezone.utc),
            ),
        ]
    )
    db.commit()

    stats = client.get("/api/stats").json()
    assert stats["total_calls"] == 2
    assert stats["total_contacts"] == 1
    assert stats["avg_duration_seconds"] == 90
    assert stats["dispositions"] == {"interested": 1, "callback": 1}
