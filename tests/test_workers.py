from dbaide.desktop.workers import ServiceWorker


def test_service_worker_forwards_project_instance_progress():
    class FakeService:
        def dispatch(self, action, payload):
            assert action == "project_instance"
            payload["progress"]({"title": "listing tables"})
            return {"ok": True}

    worker = ServiceWorker(FakeService(), "project_instance", {})
    progress = []
    done = []
    worker.signals.progress.connect(progress.append)
    worker.signals.done.connect(lambda action, result: done.append((action, result)))

    worker.run()

    assert progress == [{"title": "listing tables"}]
    assert done == [("project_instance", {"ok": True})]
