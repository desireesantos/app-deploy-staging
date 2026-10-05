def test_index_returns_hello_world(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.content == b"Hello, World!"


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_intentionally_broken(client):
    # Intentionally failing test to verify CI blocks the merge. Remove before merging.
    response = client.get("/")

    assert response.content == b"Goodbye, World!"
