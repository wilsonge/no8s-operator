# Secret Stores

The operator supports pluggable secret store backends.  Instead of reading
sensitive values directly from environment variables everywhere, components
call the active secret store, which can delegate to Vault, AWS Secrets Manager,
or any third-party backend.

Only **one** secret store is active at a time, selected via the
`SECRET_STORE_PLUGIN` environment variable.  The store is initialised before
any other plugin so that startup configuration can use it.

---

## Built-in backends

### `env` (default)

Reads secrets from environment variables.  No extra dependencies required.

```
SECRET_STORE_PLUGIN=env   # this is the default
```

### `vault`

Reads from HashiCorp Vault KV v2 via the Vault HTTP API.  Uses `aiohttp`
(already a core dependency — no extra packages needed).

```
SECRET_STORE_PLUGIN=vault
VAULT_ADDR=https://vault.example.com:8200
VAULT_TOKEN=s.xxxxxxxxxxxx
VAULT_NAMESPACE=my-namespace   # optional, Vault Enterprise only
VAULT_MOUNT=secret             # KV v2 mount path, default: secret
```

Secrets must be stored with a `value` field:

```bash
vault kv put secret/DB_PASSWORD value=hunter2
```

Then retrieved with key `DB_PASSWORD`.

### `aws_secrets_manager`

Reads from AWS Secrets Manager.  Requires `boto3`:

```bash
pip install "no8s-operator[aws]"
```

```
SECRET_STORE_PLUGIN=aws_secrets_manager
AWS_REGION=eu-west-1
# Standard AWS credentials chain applies (instance role, env vars, ~/.aws/credentials)
```

---

## Using the secret store in plugins

Action plugins and reconciler plugins can retrieve secrets at runtime:

```python
from plugins.registry import get_secret_store

class MyActionPlugin(ActionPlugin):
    async def initialize(self, config):
        store = await get_secret_store()
        self._api_token = await store.get_secret("MY_API_TOKEN")
```

`get_secret` raises `KeyError` if the secret is not found, so callers should
handle that case.

---

## Writing a custom secret store plugin

1. Implement `SecretStorePlugin`:

```python
from plugins.secrets.base import SecretStorePlugin

class MyCustomStore(SecretStorePlugin):
    @property
    def name(self) -> str:
        return "my_custom_store"

    @property
    def version(self) -> str:
        return "1.0.0"

    async def initialize(self, config):
        # set up connections / load credentials
        ...

    async def get_secret(self, key: str) -> str:
        # look up and return the secret value
        ...

    @classmethod
    def load_config_from_env(cls):
        return {"api_url": os.getenv("MY_STORE_URL", "")}
```

2. Register the entry point in your `pyproject.toml`:

```toml
[project.entry-points."no8s.secret_stores"]
my_custom_store = "mypkg.stores:MyCustomStore"
```

3. Install your package alongside the operator and set:

```
SECRET_STORE_PLUGIN=my_custom_store
```