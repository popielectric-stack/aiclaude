# Implementation Plan: AI DevOps & Coding Agent

## Overview

This plan converts the design into a series of incremental, code-only steps that build the AI DevOps & Coding Agent as a Python 3.11+ project under the `agent/` package (Requirement 22). Each step builds on prior steps and ends by wiring components together so there is no orphaned code.

The build order follows the design's layering: shared data models and project scaffolding first; then the persistence & observability layer (`Config_Loader`, `Logger`, `Memory_Store`, `Server_Registry`); then the control layer (`Security_Manager`, `LLM_Client`, `Agent_Loop`); then the nine tool components; then the `Telegram_Interface`; and finally `main.py` startup wiring plus `requirements.txt` and `README.md`.

Implementation language: **Python 3.11+** (as specified in Requirement 22.4 and the design's Technology Stack). Property-based tests use **hypothesis**; unit/integration/smoke tests use **pytest**. Every correctness property (1–32) is implemented by exactly one property-based test, tagged with `# Feature: ai-devops-coding-agent, Property {number}: {property_text}` and configured with `@settings(max_examples=100)` or higher.

All implementation tasks MUST produce production-ready, runnable code with no placeholders, stubs, TODO markers, or pseudocode (Requirement 22.6).

## Tasks

- [x] 1. Establish project scaffolding and shared data models
  - [x] 1.1 Create the `agent/` package structure and dependency manifest
    - Create the package layout: `agent/__init__.py`, `agent/database/__init__.py`, `agent/memory/__init__.py`, `agent/telegram/__init__.py`, `agent/llm/__init__.py`, `agent/tools/__init__.py`, and the `logs/` directory (with a `.gitkeep`)
    - Create `requirements.txt` pinning: `openai`, `python-telegram-bot>=20`, `paramiko`, `requests`, `playwright`, `pydantic>=2`, `cryptography`, plus dev deps `hypothesis`, `pytest`, `pytest-asyncio`, `requests-mock`
    - Create `tests/__init__.py` and a `pytest`/`hypothesis` configuration (e.g. `pyproject.toml` or `pytest.ini`)
    - _Requirements: 22.1, 22.2, 22.3, 22.5, 22.8_

  - [x] 1.2 Define shared data models and the uniform tool contract
    - Create `agent/models.py` with `ToolResult` (`success`, `data`, `error`), `Decision` (`kind` ∈ {SELECT_TOOL, TASK_COMPLETE, INVALID_RESPONSE, FAILURE, FEATURE_UNAVAILABLE}, `tool_name`, `arguments`, `message`), and `Task` (`id`, `instruction`, `status` ∈ {IN_PROGRESS, COMPLETE, FAILED, STOPPED_LIMIT}, `executed_tools`, `iteration_count`, `result_report`)
    - Implement constructors/helpers for success and error `ToolResult` envelopes used by all tools
    - _Requirements: 7.3, 8.9, 10.6, 22.6_

- [x] 2. Implement Config_Loader
  - [x] 2.1 Implement the Pydantic-validated configuration loader
    - Create `agent/config.py` with a Pydantic `Config` model (`telegram_bot_token`, `owner_id`, `api_key`, `base_url`, `model`, derived `llm_enabled`)
    - Implement `load()` reading only from `os.environ`, treating absent/empty/whitespace-only values as not provided; terminate startup (logging the named missing variable) when `TELEGRAM_BOT_TOKEN` is missing; complete startup with `llm_enabled = False` and log each missing variable when any of `API_KEY`/`BASE_URL`/`MODEL` is missing
    - Implement `check_python_version()` that halts with an "unsupported Python version" message on runtimes earlier than 3.11
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 22.4, 22.9_

  - [x]* 2.2 Write property test for configuration provisioning detection
    - **Property 1: Configuration provisioning detection**
    - **Validates: Requirements 1.1, 1.3**

  - [x]* 2.3 Write unit tests for configuration edge cases
    - Missing-token startup termination, Pydantic type validation failures, and unsupported Python version halt
    - _Requirements: 1.2, 1.5, 22.9_

- [x] 3. Implement Logger
  - [x] 3.1 Implement the file-based activity Logger
    - Create `agent/logger.py` that creates `logs/` if absent before the first write; records command text + timestamp, command result with exit code and output capped at 1,000,000 characters plus a truncation indicator, and tool name/arguments/timestamp; each entry carries exactly one severity from {info, warning, error} and a category
    - On write failure, return/emit an error indication without terminating the Agent
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7_

  - [x]* 3.2 Write property test for log output capping
    - **Property 27: Log output capping**
    - **Validates: Requirements 20.3**

  - [x]* 3.3 Write property test for log record completeness
    - **Property 28: Log record completeness**
    - **Validates: Requirements 20.2, 20.4, 20.5**

  - [x]* 3.4 Write unit test for log write-failure continuation
    - Verify an unwritable `logs/` directory yields an error indication and does not terminate the Agent
    - _Requirements: 20.7_

- [x] 4. Implement database layer and Memory_Store
  - [x] 4.1 Implement the SQLite database connection and schema
    - Create `agent/database/` modules that open the SQLite database and create the `conversations`, `servers`, `projects`, `task_history`, and `credentials` tables (with the CHECK constraints from the design) using `CREATE TABLE IF NOT EXISTS`
    - Provide a transaction helper so each persistence operation runs in a single transaction with rollback on failure
    - _Requirements: 19.1, 22.5_

  - [x] 4.2 Implement the Memory_Store
    - Create `agent/memory/store.py` with `save_conversation(message, response)`, `save_task(description, tools, outcome)`, `get_recent_context(n=20)` (ORDER BY id DESC, returns all when fewer than n), `clear_conversations()` (deletes conversations only), and `load_all()` on restart
    - On persistence failure, roll back and return an error indication that preserves prior records without partial modification
    - _Requirements: 19.2, 19.3, 19.4, 19.5, 19.6, 19.7_

  - [x]* 4.3 Write property test for conversation persistence round trip across restart
    - **Property 23: Conversation persistence round trip across restart**
    - **Validates: Requirements 19.2, 19.6**

  - [x]* 4.4 Write property test for task-history persistence round trip
    - **Property 24: Task-history persistence round trip**
    - **Validates: Requirements 19.3**

  - [x]* 4.5 Write property test for recent-context ordering and limit
    - **Property 25: Recent-context ordering and limit**
    - **Validates: Requirements 19.4, 4.3, 4.10**

  - [x]* 4.6 Write property test for clear retaining non-conversation records
    - **Property 26: Clear retains non-conversation records**
    - **Validates: Requirements 19.5, 4.4**

  - [x]* 4.7 Write unit test for persistence-failure rollback
    - Verify a failing transaction preserves prior records without partial modification
    - _Requirements: 19.7_

- [x] 5. Implement Security_Manager
  - [x] 5.1 Implement access control, credential crypto, and confirmation logic
    - Create `agent/security.py` with `authorize(sender_id)` (forward iff Owner id is non-empty and equals sender; logs rejected sender id + timestamp), `encrypt`/`decrypt` (Fernet; abort store on encrypt failure, abort dependent op on decrypt failure, never persist plaintext), `is_destructive(tool_name, args)`, `interpret_confirmation(text)` → {YES, NO, AMBIGUOUS}, and `authorize_server_target(server_name)`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 11.4, 21.1, 21.2, 21.8, 21.9, 21.10_

  - [x]* 5.2 Write property test for owner-only authorization
    - **Property 2: Owner-only authorization**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

  - [x]* 5.3 Write property test for credential encryption round trip
    - **Property 20: Credential encryption round trip**
    - **Validates: Requirements 11.4, 21.1, 21.2**

  - [x]* 5.4 Write property test for destructive-action confirmation gating
    - **Property 21: Destructive-action confirmation gating**
    - **Validates: Requirements 21.5, 21.6, 21.7, 21.11**

  - [x]* 5.5 Write property test for unauthorized-server blocking
    - **Property 22: Unauthorized-server blocking**
    - **Validates: Requirements 12.8, 21.8**

  - [x]* 5.6 Write unit tests for encrypt/decrypt failure aborts
    - Verify encrypt failure aborts the store without writing plaintext and decrypt failure aborts the dependent operation
    - _Requirements: 21.9, 21.10_

- [x] 6. Implement Server_Registry
  - [x] 6.1 Implement the Server_Registry over Memory_Store
    - Create `agent/memory/server_registry.py` with `register(server)` validating name (1–100), host (non-empty), port (int 1–65535), username (1–100), and at least one credential; rejecting duplicate names; encrypting the secret via Security_Manager before persistence; and `list_servers()` returning name/host/port/username while excluding secrets; capacity ≥ 2 up to 100
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 4.6_

  - [x]* 6.2 Write property test for server registration validation
    - **Property 17: Server registration validation**
    - **Validates: Requirements 11.1, 11.7**

  - [x]* 6.3 Write property test for duplicate server name rejection
    - **Property 18: Duplicate server name rejection**
    - **Validates: Requirements 11.8**

  - [x]* 6.4 Write property test for secret exclusion in listings
    - **Property 19: Secret exclusion in listings**
    - **Validates: Requirements 11.6, 4.6**

  - [x]* 6.5 Write unit test for server-capacity bounds
    - Verify storing 2 up to 100 servers succeeds at the boundaries
    - _Requirements: 11.5_

- [ ] 7. Checkpoint - persistence & security layer
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement File_Manager tool
  - [x] 8.1 Implement file and directory operations
    - Create `agent/tools/files.py` with `read_file`, `write_file` (creates parent dirs, create/overwrite), `append_file` (preserves prior content), `delete_file`, `create_directory` (idempotent success if exists, creates parents), `list_directory` (direct entries, empty list when empty), and `search_files` (recursive, case-insensitive substring over name or contents)
    - Missing-path operations return an error identifying the path and leave the filesystem unchanged; every operation returns a `ToolResult`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 18.1, 18.2, 18.9, 18.10_

  - [x]* 8.2 Write property test for file write/read round trip
    - **Property 7: File write/read round trip**
    - **Validates: Requirements 8.1, 8.2**

  - [x]* 8.3 Write property test for append preserving prior content
    - **Property 8: Append preserves prior content**
    - **Validates: Requirements 8.3**

  - [x]* 8.4 Write property test for directory creation idempotence
    - **Property 9: Directory creation idempotence**
    - **Validates: Requirements 8.5**

  - [x]* 8.5 Write property test for directory listing fidelity
    - **Property 10: Directory listing fidelity**
    - **Validates: Requirements 8.6**

  - [x]* 8.6 Write property test for search soundness and completeness
    - **Property 11: Search soundness and completeness**
    - **Validates: Requirements 8.7**

  - [x]* 8.7 Write property test for missing-path safety
    - **Property 12: Missing-path safety**
    - **Validates: Requirements 8.8, 18.9**

  - [x]* 8.8 Write property test for create-existing safety
    - **Property 13: Create-existing safety**
    - **Validates: Requirements 18.10**

- [x] 9. Implement Terminal_Executor tool
  - [x] 9.1 Implement command execution and resource reporting
    - Create `agent/tools/terminal.py` with `run_command` (stdout/stderr each capped at 1 MB, exit code, 300 s timeout terminates and returns partial output), `get_processes`, `check_disk_usage` (MB used/available per filesystem), `check_memory_usage` (MB), and `check_cpu_usage` (1 s sample, 0–100%)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 18.7, 18.11_

  - [x]* 9.2 Write property test for command result capping and exit-code fidelity
    - **Property 14: Command result capping and exit-code fidelity**
    - **Validates: Requirements 9.1, 9.6**

  - [x]* 9.3 Write property test for CPU utilization bound
    - **Property 15: CPU utilization bound**
    - **Validates: Requirements 9.5**

  - [x]* 9.4 Write unit test for command timeout and test-command failure
    - Verify a long command is terminated at 300 s with partial output, and an unrunnable test command returns an error
    - _Requirements: 9.7, 18.11_

- [x] 10. Implement Git_Manager tool
  - [x] 10.1 Implement Git version-control operations
    - Create `agent/tools/git.py` with `clone_repo` (rejects non-empty existing dest), `commit_changes`, `push_changes`, `pull_changes` (merge conflict → error listing conflicting files, preserves local changes), and `create_branch`; each returns success/failure with Git's output; 300 s timeout for clone/push/pull
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 10.10_

  - [x]* 10.2 Write property test for clone into non-empty destination rejection
    - **Property 16: Clone into non-empty destination is rejected**
    - **Validates: Requirements 10.9**

  - [x]* 10.3 Write unit tests for git merge-conflict and timeout handling
    - Verify merge conflict returns conflicting files and preserves local changes, and clone/push/pull timeout returns a timeout error identifying the operation
    - _Requirements: 10.8, 10.10_

- [ ] 11. Checkpoint - local tools (files, terminal, git)
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Implement SSH_Manager tool
  - [x] 12.1 Implement SSH connection, command execution, and file transfer
    - Create `agent/tools/ssh.py` using `paramiko` with a 30 s connect timeout; authenticate via decrypted key or password from the Server_Registry; implement `run_remote_command`, `upload_file`, `download_file`, and `sync_project` (recursive)
    - Handle unregistered server, connection failure, auth failure, and transfer failure (leave destination unchanged) with specific error `ToolResult`s
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8, 12.9, 12.10, 12.11_

  - [x]* 12.2 Write integration tests for SSH operations
    - Exercise connect/command/upload/download/sync, plus connection-failure and auth-failure paths, against a local/containerized SSH server or test double
    - _Requirements: 12.1, 12.4, 12.5, 12.6, 12.7, 12.9, 12.10, 12.11_

- [x] 13. Implement Pterodactyl_Manager tool
  - [x] 13.1 Implement Pterodactyl panel operations
    - Create `agent/tools/pterodactyl.py` using `requests` authenticated by a stored API key; implement `list_servers`, `upload_file`, `download_file`, `power(start|stop|restart)`, `console_output` (≤ 100 lines), and `send_console_command`
    - Transfers retry up to 3 attempts, ≥ 2 s apart, 30 s per-attempt cap; auth failure and invalid reference abort without changing state
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 13.8, 13.9, 13.10_

  - [ ]* 13.2 Write property test for Pterodactyl transfer retry policy
    - **Property 31: Pterodactyl transfer retry policy**
    - **Validates: Requirements 13.8**

  - [x]* 13.3 Write integration tests for Pterodactyl against mocked HTTP endpoints
    - Cover success, auth-failure, and invalid-reference paths using `requests-mock`
    - _Requirements: 13.2, 13.5, 13.9, 13.10_

- [x] 14. Implement cPanel_Manager tool
  - [x] 14.1 Implement cPanel hosting operations
    - Create `agent/tools/cpanel.py` using `requests` authenticated by a stored API token; implement `upload_file` (≤ 100 MB), `download_file`, `create_database`, `create_subdomain`, and `deploy_website`
    - Missing token aborts; missing path rejects without modifying files; 30 s per-operation timeout; API errors returned while independent operations still succeed
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.10_

  - [x]* 14.2 Write integration and unit tests for cPanel
    - Cover auth-failure, missing-path, timeout, and the 100 MB upload boundary against mocked HTTP endpoints
    - _Requirements: 14.2, 14.3, 14.8, 14.9_

- [x] 15. Implement Docker_Manager tool
  - [x] 15.1 Implement Docker container and image operations
    - Create `agent/tools/docker.py` with `list_containers` (all run states; empty list when none), `start|stop|restart` (≤ 10 s for stop/restart, returns resulting run state), `build_image(context, tag)`, and `deploy_container(tag)` (returns container id)
    - Errors leave targets unchanged; missing container/image returns not-found; failed build creates/tags nothing
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 15.8_

  - [x]* 15.2 Write property test for Docker container-list completeness
    - **Property 29: Docker container-list completeness**
    - **Validates: Requirements 15.1, 15.8**

  - [x]* 15.3 Write property test for Docker missing-reference safety
    - **Property 30: Docker missing-reference safety**
    - **Validates: Requirements 15.6**

  - [x]* 15.4 Write unit/integration test for Docker build failure
    - Verify an invalid build context returns a build-failure error and creates/tags no image
    - _Requirements: 15.7_

- [x] 16. Implement Browser_Automation tool
  - [x] 16.1 Implement Playwright-controlled browser operations
    - Create `agent/tools/browser.py` with `browser_open(url)` (≤ 30 s nav), `browser_click(selector)`, `browser_type(selector, text ≤ 10000 chars)`, `browser_extract_text(selector)` (empty string when no text), and `browser_screenshot()` (returns image file path)
    - Any failure returns an error describing it, identifying the selector/URL, and leaves page state unchanged
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7_

  - [x]* 16.2 Write integration and unit tests for browser automation
    - Exercise open/click/type/extract/screenshot against a static local page, plus the empty-text and 10,000-character edges and a no-match selector error
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7_

- [x] 17. Implement Deployment_Manager tool
  - [x] 17.1 Implement build/upload/deploy/restart/verify orchestration
    - Create `agent/tools/deployment.py` composing File_Manager, SSH_Manager, Terminal_Executor, and Browser_Automation; implement `build(project)` (≤ 600 s), `upload(target)`, `deploy(target)`, `restart_service(target)` (≤ 120 s), and `verify()` (successful response within ≤ 30 s)
    - Failures leave existing active artifacts unchanged and surface notifications to the Owner
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9_

  - [x]* 17.2 Write integration tests for the deployment pipeline
    - Cover build, upload, deploy, restart, and verify success/failure against a local target
    - _Requirements: 17.1, 17.3, 17.5, 17.6, 17.8_

- [ ] 18. Checkpoint - remote and external tools
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 19. Implement LLM_Client and tool catalog
  - [ ] 19.1 Build the OpenAI-style tool catalog
    - Create `agent/llm/catalog.py` defining OpenAI function-calling JSON schemas for every tool across all nine tool components, used by the model for selection
    - _Requirements: 6.4_

  - [ ] 19.2 Implement the LLM_Client
    - Create `agent/llm/client.py` using the OpenAI SDK pointed at `BASE_URL`, authenticated by `API_KEY`, with model `MODEL`; implement `decide(task_context, tool_catalog)` (valid tool+args → SELECT_TOOL; unknown tool/unparseable args → INVALID_RESPONSE logged; endpoint error or no response within 120 s → FAILURE logged + Owner notified; disabled → FEATURE_UNAVAILABLE without a network call) and `analyze(tool_result)`; include the Indonesian/English system prompt
    - _Requirements: 5.1, 5.2, 5.3, 6.1, 6.2, 6.3, 6.5, 6.6, 6.7, 6.8_

  - [ ]* 19.3 Write property test for tool-decision classification
    - **Property 4: Tool-decision classification**
    - **Validates: Requirements 6.5, 6.6**

  - [ ]* 19.4 Write property test for verbatim message and response passthrough
    - **Property 3: Verbatim message and response passthrough**
    - **Validates: Requirements 5.1, 5.2**

  - [ ]* 19.5 Write unit tests for LLM-disabled reply and endpoint failure
    - Verify FEATURE_UNAVAILABLE is returned without a network call when disabled, and endpoint error/timeout yields FAILURE with Owner notification
    - _Requirements: 1.6, 6.7, 6.8_

- [ ] 20. Implement Agent_Loop
  - [ ] 20.1 Implement the task execution loop
    - Create `agent/agent_loop.py` with `run_task(instruction)`: create a Task, gather recent context from Memory_Store, then loop request decision → confirm if destructive → execute tool → analyze result; stop on completion, the 25-iteration limit (send partial report), a tool raising (log + feed failure result back to the model + keep running), or an LLM decision failure (stop + report); persist conversation and task-history on completion
    - _Requirements: 2.4, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9_

  - [ ]* 20.2 Write property test for agent-loop iteration bound
    - **Property 5: Agent-loop iteration bound**
    - **Validates: Requirements 7.6, 7.7**

  - [ ]* 20.3 Write property test for tool-error resilience
    - **Property 6: Tool-error resilience**
    - **Validates: Requirements 2.4, 7.8**

- [ ] 21. Implement Telegram_Interface
  - [ ] 21.1 Implement the slash-command dispatcher
    - Create `agent/telegram/commands.py` handling `/start`, `/status`, `/memory`, `/clear`, `/projects`, `/servers`, `/deploy <id>` (unknown id → error identifying it, no deployment started; missing id → error; valid id → begin deployment Task), `/logs`, and unrecognized commands
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11_

  - [ ] 21.2 Implement the Telegram connection, message routing, and confirmation flow
    - Create `agent/telegram/interface.py` using `python-telegram-bot` (asyncio): maintain the connection with timestamped reconnect logging and retries ≤ 10 s; route inbound messages through `Security_Manager.authorize`; reply "unavailable" when LLM features are disabled; forward free-text to the Agent_Loop; send responses verbatim; implement `request_confirmation` with 120 s timeout and ≤ 2 re-prompts
    - _Requirements: 2.2, 2.3, 3.1, 3.2, 5.2, 6.8, 21.3, 21.4, 21.5, 21.6, 21.7, 21.11_

  - [ ]* 21.3 Write property test for unknown deploy target rejection
    - **Property 32: Unknown deploy target rejection**
    - **Validates: Requirements 4.9**

  - [ ]* 21.4 Write unit tests for command help and unknown-command handling
    - Verify `/start` lists commands with descriptions and an unrecognized command lists available commands
    - _Requirements: 4.1, 4.11_

  - [ ]* 21.5 Write integration test for Telegram reconnection behavior
    - Verify a simulated dropped connection produces a timestamped reconnect log and retries at intervals ≤ 10 s
    - _Requirements: 2.3_

- [ ] 22. Wire the Agent together and implement startup
  - [ ] 22.1 Implement `main.py` startup and component wiring
    - Create `agent/main.py` that runs `check_python_version()`, loads `Config`, initializes Logger, database, Memory_Store, Server_Registry, Security_Manager, LLM_Client, all tools, Agent_Loop, and Telegram_Interface, then runs as a single long-lived process with task-boundary isolation so an unhandled task error marks the task failed, logs it, and keeps the process running
    - _Requirements: 2.1, 2.4, 22.6, 22.7_

  - [ ]* 22.2 Write smoke tests for project structure and startup
    - Verify the `agent/` package layout and `tools/` modules, presence of `logs/`/`requirements.txt`/`README.md`, absence of placeholders/stubs/TODOs, and that launching `main.py` initializes database/memory/telegram/llm/tools without unhandled exceptions
    - _Requirements: 22.1, 22.2, 22.3, 22.6, 22.7, 20.6_

- [ ] 23. Author project documentation
  - [ ] 23.1 Write `README.md`
    - Document the environment variables, installation (`requirements.txt` + `playwright install`), running on an Ubuntu VPS, the Telegram commands, and the project structure
    - _Requirements: 22.3_

- [ ] 24. Final checkpoint - full test suite
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks (property, unit, integration, smoke) and can be skipped for a faster MVP; all non-`*` tasks are core implementation and must be completed.
- Each correctness property (1–32) is implemented by exactly one property-based test using `hypothesis`, tagged `# Feature: ai-devops-coding-agent, Property {number}: {property_text}`, running ≥ 100 examples, with external boundaries isolated by in-memory fakes/mocks per the Testing Strategy.
- Property-to-task map: P1→2.2, P2→5.2, P3→19.4, P4→19.3, P5→20.2, P6→20.3, P7→8.2, P8→8.3, P9→8.4, P10→8.5, P11→8.6, P12→8.7, P13→8.8, P14→9.2, P15→9.3, P16→10.2, P17→6.2, P18→6.3, P19→6.4, P20→5.3, P21→5.4, P22→5.5, P23→4.3, P24→4.4, P25→4.5, P26→4.6, P27→3.2, P28→3.3, P29→15.2, P30→15.3, P31→13.2, P32→21.3.
- Each task references specific requirement/property clauses for traceability; checkpoints (tasks 7, 11, 18, 24) ensure incremental validation.
- No placeholders, stubs, TODO markers, or pseudocode are permitted in the final implementation (Requirement 22.6).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["3.1", "4.1"] },
    { "id": 2, "tasks": ["2.1", "4.2", "5.1", "8.1", "9.1", "10.1", "15.1", "16.1"] },
    { "id": 3, "tasks": ["6.1", "13.1", "14.1", "19.1", "2.2", "2.3", "3.2", "3.3", "3.4", "8.2", "8.3", "8.4", "8.5", "8.6", "8.7", "8.8", "9.2", "9.3", "9.4", "10.2", "10.3", "15.2", "15.3", "15.4", "16.2", "4.3", "4.4", "4.5", "4.6", "4.7", "5.2", "5.3", "5.4", "5.5", "5.6"] },
    { "id": 4, "tasks": ["12.1", "19.2", "6.2", "6.3", "6.4", "6.5", "13.2", "13.3", "14.2"] },
    { "id": 5, "tasks": ["17.1", "20.1", "12.2", "19.3", "19.4", "19.5"] },
    { "id": 6, "tasks": ["21.1", "17.2", "20.2", "20.3"] },
    { "id": 7, "tasks": ["21.2"] },
    { "id": 8, "tasks": ["22.1", "21.3", "21.4", "21.5"] },
    { "id": 9, "tasks": ["22.2", "23.1"] }
  ]
}
```
