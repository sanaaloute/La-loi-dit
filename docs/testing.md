# Load testing

Locust:

```python
# locustfile.py
from locust import HttpUser, task, between

class LegalUser(HttpUser):
    wait_time = between(1, 3)

    @task
    def chat(self):
        self.client.post("/api/v1/chat", json={
            "query": "Quels sont les droits d'un salarié licencié ?"
        })
```

`locust -f locustfile.py --host http://localhost:8000 -u 50 -r 5`

k6:

```js
// chat.js
import http from "k6/http";
import { sleep } from "k6";
export const options = { vus: 50, duration: "2m" };
export default function () {
  http.post("http://localhost:8000/api/v1/chat",
    JSON.stringify({ query: "Quels sont les droits d'un salarié licencié ?" }),
    { headers: { "Content-Type": "application/json" } });
  sleep(1);
}
```

Watch the Grafana latency and error panels while load-testing; the mock
provider isolates pipeline overhead, a real provider measures true
end-to-end cost.
