# AI Safety Restrictions (MANDATORY)

These rules must be followed at all times when interacting with this repository.

---

## 🚫 Environment Files & Secrets

You are NOT permitted to:

* Read `.env` files
* Access `.env.*` files
* Output environment variables
* Log credentials or tokens
* Use secrets in generated code
* Suggest exposing API keys

If a task appears to require secrets:
➡️ STOP and ask the user for approval.

You must treat all configuration files as potentially sensitive.

---

## 🧨 Destructive Actions Policy

You MUST request explicit user approval BEFORE:

* Deleting files
* Renaming files or directories
* Moving files
* Overwriting existing files
* Replacing configuration files
* Resetting git history
* Cleaning build outputs
* Modifying data schemas
* Dropping tables
* Performing bulk edits

You must NEVER execute:

* rm -rf
* git reset --hard
* git clean -fd
* Any command that permanently deletes data

If unsure whether an action is destructive:
➡️ Ask before proceeding.

---

## 📦 Dependency Installation Policy

You MUST request explicit approval BEFORE:

* Installing packages
* Adding dependencies
* Updating dependency versions
* Running package managers (npm, pip, apt, etc.)
* Executing install scripts
* Adding build tools

You must NEVER:

* Install packages automatically
* Use `curl | bash`
* Download unsigned binaries
* Add dependencies from GitHub URLs
* Run remote setup scripts

Explain WHY the dependency is needed before requesting approval.

---

## ⚙️ Command Execution

Before running any shell command you must:

1. Explain what the command does
2. State whether it modifies files
3. Ask for user approval

---

## 🔒 Safety Priority

If any request conflicts with these rules:

➡️ The rules take priority over task completion.

Failure to follow these restrictions is considered a critical error.
