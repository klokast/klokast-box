
 #TODO: review Ansible Vault as OpenAI API key storage

# Install Codex dependencies

- As user `neo`:
```
sudo apt update
sudo apt install -y bubblewrap curl git build-essential ca-certificates
```

# Install codex

- As user `agent`
```
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash

export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
[ -s "$NVM_DIR/bash_completion" ] && \. "$NVM_DIR/bash_completion"

nvm install --lts
nvm alias default 'lts/*'
nvm use default
node -v
npm -v
which node
which npm
sudo apt update
sudo apt upgrade -y
npm install -g @openai/codex
codex --version

codex login --device-auth
mkdir -p ~/src/<orga>/<repo>
cd ~/src/<orga>/<repo>
codex resume --all
```

# Setup codex via API as backup

 - On `https://platform.openai.com/account/billing`, set a API usage limit.
 - On `https://platform.openai.com/api-keys`:
	- create a "project" as per repo name :  `orga/repo` (e.g. `klokast-klokast`
	- create a new secret key:
		- name: `hetzner-ops-codex` (cloud provider - machine hostname - user name)
	    - "restricted" > list models: read, model capabilities: mixed, responses > write, everything else is "None"
```
Restricted
	List models                         Read
	Model capabilities
	  Responses (/v1/responses)         Write
	  Text-to-speech                    None
	  Realtime                          None
	  Chat completions                  None
	  Embeddings                        None
	  Images                            None
	  Moderations                       None
	Assistants                          None
	Threads                             None
	Evals                               None
	Fine-tuning                         None
	Files                               None
	Videos                              None
	Vector Stores                       None
	Prompts                             None
	Datasets                            None
```
 - then copy the key!  Be careful and not leak the key into the codex user bash history, and into the Codex agent history!

```
mkdir -p /home/codex/.config/secrets
chmod 700 /home/codex/.config/secrets
vi /home/codex/.config/secrets/openai.env
	OPENAI_API_KEY='xxxxxx_api_key_xxxxx'
chmod 600 ~/.config/codex/env
vi ~/.bashrc            # add this:
	set -a
	. /home/codex/.config/secrets/openai.env
	set +a

	if [ -f "$HOME/.config/codex/env" ]; then
	 . "$HOME/.config/codex/env"
	fi

	codexapi() {
	  command codex logout >/dev/null 2>&1 || true
	  command codex -c preferred_auth_method="apikey" "$@"
	}
chmod 600 /home/codex/.config/secrets/openai.env
chown -R codex:codex /home/codex/.config/secrets

source ~/.bashrc

bash -n ~/.config/codex/env
bash -n ~/.bashrc
source ~/.bashrc
printf '%s\n' "${OPENAI_API_KEY:+set}"      # should print "set"
```

# if Codex runs out of `plus` tokens, switch to API mode:

```
codexapi           # logs out from codex then start the api-fueled codex
/status            # should show xxxxxxx
```

# Guardrails on OpenAI Codex
`mkdir -p ~/.codex`
```
vi ~/.codex/config.toml
	model_reasoning_effort = "xhigh"
	approvals_reviewer = "user"
	personality = "pragmatic"
	plan_mode_reasoning_effort = "xhigh"

	approval_policy = "never"
	sandbox_mode = "danger-full-access"

	[projects."/home/codex/src/klokast/klokast-box"]
	trust_level = "trusted"

	[notice]
	hide_full_access_warning = true

	[tui]
	status_line = ["model-with-reasoning", "context-remaining", "current-dir", "session-id", "run-state"]
```
