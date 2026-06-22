from pathlib import Path


class LLM:
    def __init__(self, config_path):
        cfg = self._load_config(config_path)
        from openai import OpenAI

        self.model = cfg.get("llm_name") or cfg.get("model")
        self.temperature = float(cfg.get("temperature", 0))
        self.client = OpenAI(api_key=cfg.get("key"), base_url=cfg.get("openai_base_url"))

    def query(self, system_prompt, history, user_prompt):
        rsp = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": history},
                {"role": "user", "content": user_prompt},
            ],
        )
        return rsp.choices[0].message.content

    @staticmethod
    def _load_config(path):
        text = Path(path).read_text(encoding="utf-8")
        try:
            import yaml

            return yaml.safe_load(text) or {}
        except Exception:
            cfg = {}
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if ":" in line and not line.startswith("-"):
                    k, v = line.split(":", 1)
                    cfg[k.strip()] = v.strip().strip('"\'')
            return cfg
