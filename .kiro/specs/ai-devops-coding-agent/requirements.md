# Requirements Document

## Introduction

The AI DevOps & Coding Agent is a Python application that runs continuously (24/7) on a Linux Ubuntu Virtual Private Server (VPS). The owner controls the agent exclusively through a Telegram chat. The agent forwards the owner's natural-language instructions (in Indonesian or English) to a Claude Opus 4.8 model exposed through an OpenAI-compatible API endpoint, then carries out the resulting plan by invoking a set of internal tools. These tools let the agent create and edit software projects, run terminal commands, manage Git repositories, administer remote servers over SSH, control Pterodactyl game/app panels, manage cPanel hosting, manage Docker containers, automate a web browser, and deploy applications.

The agent maintains persistent memory (conversations, registered servers, projects, and task history) in a SQLite database, records all activity to log files, and enforces owner-only access with encrypted credential storage and explicit confirmation before destructive operations. All runtime configuration is supplied through environment variables.

## Glossary

- **Agent**: The complete Python application that orchestrates message handling, model interaction, tool execution, memory, and logging.
- **Owner**: The single Telegram user authorized to control the Agent, identified by a configured Telegram user identifier.
- **Telegram_Interface**: The component that connects to the Telegram Bot API, receives messages, dispatches commands, and sends responses.
- **Config_Loader**: The component that reads and validates configuration values from environment variables at startup.
- **LLM_Client**: The component that communicates with the Claude Opus 4.8 model through the OpenAI-compatible API endpoint.
- **Agent_Loop**: The control loop that analyzes a task, selects a tool, executes the tool, analyzes the result, repeats until the task is complete, and reports back.
- **File_Manager**: The tool component that performs file and directory operations on the local VPS filesystem.
- **Terminal_Executor**: The tool component that runs shell commands and reports system resource usage on the local VPS.
- **Git_Manager**: The tool component that performs Git version-control operations.
- **SSH_Manager**: The tool component that connects to registered remote servers over SSH and performs remote operations.
- **Server_Registry**: The persistent store of registered remote servers and their connection details.
- **Pterodactyl_Manager**: The tool component that controls servers through the Pterodactyl panel API.
- **cPanel_Manager**: The tool component that performs hosting operations through the cPanel API.
- **Docker_Manager**: The tool component that manages Docker containers and images.
- **Browser_Automation**: The tool component that controls a headless web browser using Playwright.
- **Deployment_Manager**: The tool component that builds, uploads, deploys, restarts, and verifies application deployments.
- **Memory_Store**: The SQLite-backed component that persists conversations, server records, project records, and task history.
- **Logger**: The component that writes activity records, executed commands, and command results to log files in the `logs/` directory.
- **Security_Manager**: The component that enforces owner-only access, encrypts and decrypts stored credentials, and gates destructive operations behind confirmation.
- **Registered_Server**: A remote server whose connection details have been explicitly stored in the Server_Registry by the Owner.
- **Destructive_Action**: An operation that deletes data, stops or restarts a service or server, overwrites files, drops a database, or otherwise causes loss or interruption that cannot be trivially undone.
- **Task**: A unit of work derived from a single Owner instruction that the Agent_Loop executes to completion.
- **Configuration_Variable**: One of the environment variables `TELEGRAM_BOT_TOKEN`, `API_KEY`, `BASE_URL`, and `MODEL`.

## Requirements

### Requirement 1: Configuration From Environment Variables

**User Story:** As the Owner, I want all configuration to be supplied through environment variables, so that I can configure the Agent without editing source code or storing secrets in files.

#### Acceptance Criteria

1. WHEN the Agent starts, THE Config_Loader SHALL read the values of `TELEGRAM_BOT_TOKEN`, `API_KEY`, `BASE_URL`, and `MODEL` exclusively from the process environment variables, and SHALL treat any of these values that is absent, empty, or consists only of whitespace characters as not provided.
2. IF the `TELEGRAM_BOT_TOKEN` Configuration_Variable is not provided, THEN THE Config_Loader SHALL terminate the Agent startup before the Telegram_Interface connects to the Telegram Bot API and SHALL write a log record to the Logger that names the `TELEGRAM_BOT_TOKEN` Configuration_Variable as the missing variable.
3. IF the `API_KEY`, `BASE_URL`, or `MODEL` Configuration_Variable is not provided, THEN THE Config_Loader SHALL complete the Agent startup with the language-model features disabled and SHALL write a log record to the Logger that names each Configuration_Variable that is not provided.
4. WHEN the Agent startup completes successfully, THE Config_Loader SHALL make the loaded values available to the Telegram_Interface and the LLM_Client before the Agent begins processing messages.
5. THE Config_Loader SHALL load Configuration_Variables into a typed configuration object validated by Pydantic.
6. WHILE the language-model features are disabled, IF the Owner sends a natural-language instruction, THEN THE Telegram_Interface SHALL respond with a message indicating that the language-model features are unavailable because one or more required Configuration_Variables are not provided.

### Requirement 2: Continuous Operation on Ubuntu VPS

**User Story:** As the Owner, I want the Agent to run continuously on my Ubuntu VPS, so that I can issue commands at any time.

#### Acceptance Criteria

1. THE Agent SHALL run as a single long-lived operating-system process on Linux Ubuntu that continues running until it receives an explicit operating-system termination or stop signal.
2. WHILE the Agent process is running, THE Telegram_Interface SHALL maintain an active connection to the Telegram Bot API and SHALL continuously receive incoming messages.
3. IF the connection to the Telegram Bot API is interrupted, THEN THE Telegram_Interface SHALL write a reconnection log record that includes a timestamp to the Logger and SHALL retry establishing the connection at intervals of no more than 10 seconds until the connection is re-established.
4. IF an unhandled error occurs while processing a single Task, THEN THE Agent SHALL record the error through the Logger, mark the affected Task as failed, and SHALL remain running to accept and process subsequent messages.
5. WHILE the Agent is running on a host that provides 4 GB of RAM and 2 virtual CPUs, THE Agent SHALL operate without exceeding the available 4 GB of memory and SHALL continue running without being terminated for memory exhaustion.

### Requirement 3: Owner-Only Access Control

**User Story:** As the Owner, I want only my own Telegram account to control the Agent, so that no unauthorized person can operate my servers.

#### Acceptance Criteria

1. WHEN the Telegram_Interface receives an incoming message of any type from any sender, THE Security_Manager SHALL compare the sender's Telegram user identifier against the configured Owner identifier before the message is forwarded to the Agent_Loop.
2. IF the sender's Telegram user identifier does not match the configured Owner identifier, THEN THE Telegram_Interface SHALL discard the message without forwarding it to the Agent_Loop and SHALL respond to the sender with a notice indicating that access is denied.
3. IF the sender's Telegram user identifier does not match the configured Owner identifier, THEN THE Logger SHALL record a log entry that includes the rejected sender's Telegram user identifier and the timestamp of the rejected message.
4. WHEN the Telegram_Interface receives an incoming message whose sender's Telegram user identifier matches the configured Owner identifier, THE Agent SHALL process the message.
5. IF the configured Owner identifier is absent or empty when a message is received, THEN THE Telegram_Interface SHALL discard the message without forwarding it to the Agent_Loop and SHALL record the rejection through the Logger.

### Requirement 4: Telegram Command Handling

**User Story:** As the Owner, I want a set of Telegram commands, so that I can control and inspect the Agent directly from chat.

#### Acceptance Criteria

1. WHEN the Owner sends `/start`, THE Telegram_Interface SHALL respond with a message that lists the available commands and a description of each command.
2. WHEN the Owner sends `/status`, THE Telegram_Interface SHALL respond with the Agent uptime, the count of Registered_Servers, the count of stored projects, and the count of in-progress Tasks.
3. WHEN the Owner sends `/memory`, THE Telegram_Interface SHALL respond with a summary of the 10 most recent stored conversation records and the 10 most recent task-history records retrieved from the Memory_Store.
4. WHEN the Owner sends `/clear`, THE Memory_Store SHALL delete the stored conversation history and the Telegram_Interface SHALL respond with a confirmation that the conversation history was cleared.
5. WHEN the Owner sends `/projects`, THE Telegram_Interface SHALL respond with the list of stored projects retrieved from the Memory_Store.
6. WHEN the Owner sends `/servers`, THE Telegram_Interface SHALL respond with the list of Registered_Servers retrieved from the Server_Registry, excluding any stored credential secrets.
7. WHEN the Owner sends `/deploy` followed by a project identifier that matches a stored project in the Memory_Store, THE Deployment_Manager SHALL begin a deployment Task for the identified project and the Telegram_Interface SHALL respond with an acknowledgement.
8. IF the Owner sends `/deploy` without a project identifier, THEN THE Telegram_Interface SHALL respond with an error message that states a project identifier is required and SHALL NOT begin a deployment Task.
9. IF the Owner sends `/deploy` with a project identifier that does not match any stored project in the Memory_Store, THEN THE Telegram_Interface SHALL respond with an error message that identifies the unknown project identifier and SHALL NOT begin a deployment Task.
10. WHEN the Owner sends `/logs`, THE Telegram_Interface SHALL respond with the 20 most recent activity records retrieved from the Logger.
11. IF the Owner sends a command that is not recognized, THEN THE Telegram_Interface SHALL respond with a message that states the command is unrecognized and lists the available commands.

### Requirement 5: Indonesian Language Understanding

**User Story:** As the Owner, I want the Agent to understand instructions written in Indonesian, so that I can give commands in my preferred language.

#### Acceptance Criteria

1. WHEN the Owner sends a natural-language message that is not a recognized slash command, THE Agent SHALL forward the verbatim message content to the LLM_Client for interpretation, whether the message is written in Indonesian or English.
2. WHEN the LLM_Client returns a response to a message that the Owner wrote in Indonesian, THE Telegram_Interface SHALL send that response to the Owner without translating or altering its language.
3. THE LLM_Client SHALL include a system instruction that directs the model to interpret instructions written in Indonesian or English and to compose each response in the same language as the Owner's instruction.

### Requirement 6: Language Model Integration

**User Story:** As the Owner, I want the Agent to use the Claude Opus 4.8 model through an OpenAI-compatible endpoint, so that the Agent can reason about my instructions and select tools.

#### Acceptance Criteria

1. THE LLM_Client SHALL send requests to the OpenAI-compatible endpoint identified by the `BASE_URL` Configuration_Variable.
2. THE LLM_Client SHALL authenticate each request using the `API_KEY` Configuration_Variable.
3. THE LLM_Client SHALL set the requested model to the value of the `MODEL` Configuration_Variable.
4. WHEN the Agent_Loop requires a decision, THE LLM_Client SHALL send the current Task context and the catalog of available tools to the model.
5. WHEN the model returns a response that names a tool present in the catalog and provides parseable arguments for that tool, THE LLM_Client SHALL return the selected tool name and its arguments to the Agent_Loop.
6. IF the model returns a response that names a tool not present in the catalog or provides arguments that cannot be parsed, THEN THE LLM_Client SHALL return an invalid-response error result to the Agent_Loop and SHALL record the invalid response through the Logger.
7. IF the OpenAI-compatible endpoint returns an error response or does not respond within 120 seconds, THEN THE LLM_Client SHALL return a failure result for the current decision to the Agent_Loop, SHALL record the failure through the Logger, and THE Telegram_Interface SHALL notify the Owner that the model request failed.
8. WHILE the language-model features are disabled, IF the Agent_Loop requires a decision, THEN THE LLM_Client SHALL return a feature-unavailable result to the Agent_Loop without sending a request to the endpoint and THE Telegram_Interface SHALL notify the Owner that the language-model features are disabled.

### Requirement 7: Agent Execution Loop

**User Story:** As the Owner, I want the Agent to work through a task step by step until it is complete, so that complex requests are carried out without further prompting.

#### Acceptance Criteria

1. WHEN the Owner sends a natural-language instruction, THE Agent_Loop SHALL create a Task and request a tool selection from the LLM_Client.
2. WHEN the LLM_Client returns a selected tool and arguments, THE Agent_Loop SHALL execute the selected tool with the provided arguments.
3. WHEN a tool finishes execution, THE Agent_Loop SHALL send the tool result, including an indication of whether the tool execution succeeded or failed, to the LLM_Client for analysis.
4. WHILE the LLM_Client reports that the Task is incomplete, THE Agent_Loop SHALL select and execute the next tool.
5. WHEN the LLM_Client reports that the Task is complete, THE Agent_Loop SHALL send a result report to the Owner through the Telegram_Interface.
6. WHEN the Agent_Loop reaches 25 completed tool executions for a single Task without the LLM_Client reporting the Task complete, THE Agent_Loop SHALL stop the Task.
7. WHEN the Agent_Loop stops a Task at the 25 tool-execution limit, THE Agent_Loop SHALL send a report of the partial result accumulated for the Task to the Owner through the Telegram_Interface.
8. IF a selected tool raises an error instead of returning a result, THEN THE Agent_Loop SHALL record the failure through the Logger and send a failure result that identifies the failed tool to the LLM_Client for analysis.
9. IF the LLM_Client reports that a tool-selection request or a result-analysis request failed, THEN THE Agent_Loop SHALL stop the Task and report the failure to the Owner through the Telegram_Interface.

### Requirement 8: File Management Tools

**User Story:** As the Owner, I want the Agent to read, write, and organize files, so that the Agent can build and modify projects on the VPS.

#### Acceptance Criteria

1. WHEN the Agent_Loop invokes `read_file` with a file path, THE File_Manager SHALL return the contents of the file at the path.
2. WHEN the Agent_Loop invokes `write_file` with a file path and content, THE File_Manager SHALL create any missing parent directories named in the path and SHALL create or overwrite the file at the path with the content.
3. WHEN the Agent_Loop invokes `append_file` with a file path and content, THE File_Manager SHALL append the content to the end of the existing file at the path while preserving the file's prior content.
4. WHEN the Agent_Loop invokes `delete_file` with a file path, THE File_Manager SHALL remove the file at the path.
5. WHEN the Agent_Loop invokes `create_directory` with a directory path, THE File_Manager SHALL create the directory at the path together with any missing parent directories named in the path, and SHALL return a success result if the directory already exists.
6. WHEN the Agent_Loop invokes `list_directory` with a directory path, THE File_Manager SHALL return the names of all file and subdirectory entries contained directly in the directory at the path, and SHALL return an empty list when the directory contains no entries.
7. WHEN the Agent_Loop invokes `search_files` with a search term and a directory path, THE File_Manager SHALL return the paths of every file within the directory and its nested subdirectories whose file name or file contents contain the search term as a case-insensitive substring.
8. IF a file or directory path passed to a `read_file`, `append_file`, `delete_file`, `list_directory`, or `search_files` operation does not exist, THEN THE File_Manager SHALL return an error result that identifies the missing path and SHALL leave all existing files and directories unchanged.
9. THE File_Manager SHALL return a result that indicates success or failure to the Agent_Loop for each invoked file or directory operation.

### Requirement 9: Terminal Execution Tools

**User Story:** As the Owner, I want the Agent to run shell commands and report system resource usage, so that the Agent can operate the VPS and report its health.

#### Acceptance Criteria

1. WHEN the Agent_Loop invokes `run_command` with a command string, THE Terminal_Executor SHALL execute the command on the local VPS shell and return the captured standard output, the captured standard error, and the exit code, with the captured standard output and the captured standard error each limited to a maximum of 1 megabyte.
2. WHEN the Agent_Loop invokes `get_processes`, THE Terminal_Executor SHALL return the list of running processes with each process identifier and command name.
3. WHEN the Agent_Loop invokes `check_disk_usage`, THE Terminal_Executor SHALL return the used disk space in megabytes and the available disk space in megabytes for each mounted filesystem.
4. WHEN the Agent_Loop invokes `check_memory_usage`, THE Terminal_Executor SHALL return the used and available memory in megabytes.
5. WHEN the Agent_Loop invokes `check_cpu_usage`, THE Terminal_Executor SHALL sample processor utilization over a 1-second interval and return the utilization as a percentage value between 0 and 100.
6. IF a command executed by the Terminal_Executor returns a non-zero exit code, THEN THE Terminal_Executor SHALL return the exit code and the standard error content in the result.
7. IF a command executed by `run_command` does not complete within 300 seconds, THEN THE Terminal_Executor SHALL terminate the command and return a result that indicates the command was terminated due to timeout, including any standard output and standard error captured before termination.

### Requirement 10: Git Version Control Tools

**User Story:** As the Owner, I want the Agent to manage Git repositories, so that project code can be cloned, versioned, and synchronized with remotes.

#### Acceptance Criteria

1. WHEN the Agent_Loop invokes `clone_repo` with a repository URL and a destination path, THE Git_Manager SHALL clone the repository into the destination path.
2. WHEN the Agent_Loop invokes `commit_changes` with a commit message, THE Git_Manager SHALL stage the modified files and create a commit with the message.
3. WHEN the Agent_Loop invokes `push_changes` with a branch name, THE Git_Manager SHALL push the named branch to the configured remote.
4. WHEN the Agent_Loop invokes `pull_changes` with a branch name, THE Git_Manager SHALL pull the named branch from the configured remote.
5. WHEN the Agent_Loop invokes `create_branch` with a branch name, THE Git_Manager SHALL create the named branch.
6. THE Git_Manager SHALL attempt each requested Git operation and SHALL return a result that indicates whether the operation succeeded or failed and that includes the output produced by Git.
7. IF a Git operation returns an error, THEN THE Git_Manager SHALL return the error message produced by Git.
8. IF a `clone_repo`, `push_changes`, or `pull_changes` operation does not complete within 300 seconds, THEN THE Git_Manager SHALL terminate the operation and return a timeout error result that identifies the affected operation.
9. IF the Agent_Loop invokes `clone_repo` with a destination path that already exists and is not empty, THEN THE Git_Manager SHALL reject the operation and return an error result that identifies the destination path without modifying the existing contents of the destination path.
10. IF a `pull_changes` operation produces a merge conflict, THEN THE Git_Manager SHALL return an error result that identifies the conflicting files and SHALL preserve the local uncommitted changes.

### Requirement 11: Server Registry

**User Story:** As the Owner, I want to register multiple remote servers with their connection details, so that the Agent can manage each authorized server.

#### Acceptance Criteria

1. THE Server_Registry SHALL store, for each Registered_Server, a non-empty server name of up to 100 characters, a non-empty host address, a port number that is an integer from 1 to 65535, and a non-empty username of up to 100 characters.
2. WHERE a Registered_Server uses key-based authentication, THE Server_Registry SHALL store the associated SSH private key for that server.
3. WHERE a Registered_Server uses password-based authentication, THE Server_Registry SHALL store the associated password for that server.
4. WHEN the Owner registers a server, THE Security_Manager SHALL encrypt the SSH private key or password before the Server_Registry persists the value.
5. THE Server_Registry SHALL allow two or more Registered_Servers, up to a maximum of 100, to be stored at the same time.
6. WHEN the Owner requests the list of servers, THE Server_Registry SHALL return the server name, host address, port number, and username of each Registered_Server while excluding the stored SSH private key and password.
7. IF the Owner registers a server with an empty required field, with a port number outside the range 1 to 65535, or with neither an SSH private key nor a password, THEN THE Server_Registry SHALL reject the registration, SHALL NOT persist the server, and SHALL return an error that identifies the invalid or missing value.
8. IF the Owner registers a server whose server name matches the server name of an existing Registered_Server, THEN THE Server_Registry SHALL reject the registration, SHALL NOT overwrite the existing Registered_Server, and SHALL return an error that states the server name already exists.

### Requirement 12: SSH Server Management

**User Story:** As the Owner, I want the Agent to connect to my registered servers over SSH, so that the Agent can run commands and transfer files on those servers.

#### Acceptance Criteria

1. WHEN the Agent_Loop invokes an SSH operation that names a Registered_Server, THE SSH_Manager SHALL connect to the server using the connection details retrieved from the Server_Registry within a connection timeout of 30 seconds.
2. WHERE the named Registered_Server uses key-based authentication, THE SSH_Manager SHALL authenticate the SSH connection using the decrypted SSH private key.
3. WHERE the named Registered_Server uses password-based authentication, THE SSH_Manager SHALL authenticate the SSH connection using the decrypted password.
4. WHEN the SSH_Manager is connected to a Registered_Server and the Agent_Loop requests command execution, THE SSH_Manager SHALL run the command on the connected server and return the standard output, standard error, and exit code.
5. WHEN the Agent_Loop requests a file upload to a Registered_Server, THE SSH_Manager SHALL transfer the named local file to the named remote path on the connected server.
6. WHEN the Agent_Loop requests a file download from a Registered_Server, THE SSH_Manager SHALL transfer the named remote file to the named local path.
7. WHEN the Agent_Loop requests a project synchronization to a Registered_Server, THE SSH_Manager SHALL transfer all files and subdirectories contained within the named local project directory, recursively, to the named remote project directory.
8. IF the Agent_Loop requests an SSH operation on a server that is not present in the Server_Registry, THEN THE SSH_Manager SHALL reject the operation and return an error that states the server is not registered.
9. IF the SSH_Manager cannot establish a connection to the named Registered_Server within the connection timeout or the server is unreachable, THEN THE SSH_Manager SHALL abort the operation and return an error indicating that the connection could not be established.
10. IF authentication to the named Registered_Server fails using the configured key-based or password-based credentials, THEN THE SSH_Manager SHALL abort the connection attempt and return an error indicating that authentication failed.
11. IF a requested file upload, file download, or project synchronization cannot be completed because the named source does not exist, the named destination path is invalid, or the transfer is interrupted, THEN THE SSH_Manager SHALL abort the transfer, leave each affected destination file unchanged, and return an error indicating that the transfer failed and the reason.

### Requirement 13: Pterodactyl Panel Management

**User Story:** As the Owner, I want the Agent to control servers through the Pterodactyl panel, so that I can manage panel-hosted servers from Telegram.

#### Acceptance Criteria

1. THE Pterodactyl_Manager SHALL authenticate requests to the Pterodactyl panel using a stored Pterodactyl API key.
2. WHEN the Agent_Loop requests the Pterodactyl server list, THE Pterodactyl_Manager SHALL return the identifier and name of each server available to the API key, or return an empty list when no servers are available to the API key.
3. WHEN the Agent_Loop requests a file upload to a Pterodactyl server, THE Pterodactyl_Manager SHALL transfer the named file to the named path on the server and return a transfer-complete confirmation to the Agent_Loop.
4. WHEN the Agent_Loop requests a file download from a Pterodactyl server, THE Pterodactyl_Manager SHALL retrieve the named file from the named path on the server and return the retrieved file contents to the Agent_Loop.
5. WHEN the Agent_Loop requests a start, stop, or restart of a named Pterodactyl server, THE Pterodactyl_Manager SHALL send the corresponding power signal to the server through the Pterodactyl panel API.
6. WHEN the Agent_Loop requests the console output of a named Pterodactyl server, THE Pterodactyl_Manager SHALL return the most recent console output of the server, up to a maximum of the last 100 lines.
7. WHEN the Agent_Loop requests sending a console command to a named Pterodactyl server, THE Pterodactyl_Manager SHALL send the command to the server console through the Pterodactyl panel API.
8. IF a Pterodactyl file upload or file download fails due to a network error or a server error, THEN THE Pterodactyl_Manager SHALL retry the transfer up to 3 attempts, waiting at least 2 seconds between attempts and treating any single attempt that exceeds 30 seconds as failed, before reporting the failure with an error indication to the Agent_Loop.
9. IF the stored Pterodactyl API key is missing or is rejected by the Pterodactyl panel, THEN THE Pterodactyl_Manager SHALL abort the requested operation without modifying any server state and report an authentication-failure error indication to the Agent_Loop.
10. IF the Agent_Loop references a Pterodactyl server identifier or file path that does not exist or is not available to the API key, THEN THE Pterodactyl_Manager SHALL reject the request without modifying any server state and report an error indication identifying the invalid reference to the Agent_Loop.

### Requirement 14: cPanel Hosting Management

**User Story:** As the Owner, I want the Agent to manage cPanel hosting, so that I can deploy and administer websites from Telegram.

#### Acceptance Criteria

1. WHEN the cPanel_Manager sends a request to the cPanel API, THE cPanel_Manager SHALL authenticate the request using a stored cPanel API token.
2. IF the stored cPanel API token is missing, invalid, or expired, THEN THE cPanel_Manager SHALL abort the requested operation without modifying any hosting state and return an authentication-failure error indication to the Agent_Loop.
3. WHEN the Agent_Loop requests a file upload through cPanel, THE cPanel_Manager SHALL transfer the named file, up to a maximum size of 100 megabytes, to the named path using the cPanel File Manager API.
4. WHEN the Agent_Loop requests a file download through cPanel, THE cPanel_Manager SHALL retrieve the named file from the named path using the cPanel File Manager API.
5. WHEN the Agent_Loop requests creation of a database with a given name, THE cPanel_Manager SHALL create the database through the cPanel API.
6. WHEN the Agent_Loop requests creation of a subdomain with a given name, THE cPanel_Manager SHALL create the subdomain through the cPanel API.
7. WHEN the Agent_Loop requests a website deployment through cPanel, THE cPanel_Manager SHALL upload the website files to the named document root using the cPanel File Manager API.
8. IF a cPanel file upload, file download, or website deployment references a path that does not exist, THEN THE cPanel_Manager SHALL reject that operation and return an error identifying the missing path without modifying existing files.
9. IF a cPanel API request for a specific operation does not complete within 30 seconds, THEN THE cPanel_Manager SHALL treat that operation as failed and return a timeout error indication for that operation.
10. IF a cPanel API request for a specific operation returns an error, THEN THE cPanel_Manager SHALL return the error message produced by the cPanel API for that operation, leave the prior state for that operation unchanged, and allow independent operations that did not encounter the error to complete successfully.

### Requirement 15: Docker Container Management

**User Story:** As the Owner, I want the Agent to manage Docker containers and images, so that I can deploy and operate containerized applications.

#### Acceptance Criteria

1. WHEN the Agent_Loop requests the container list, THE Docker_Manager SHALL return, for every Docker container present on the host regardless of run state, its identifier, name, and current run state, where the run state is one of: created, running, restarting, paused, exited, or dead.
2. WHEN the Agent_Loop requests a start, stop, or restart of a named container that exists, THE Docker_Manager SHALL perform the corresponding action on that container, complete a stop or restart within 10 seconds, and return the container's resulting run state.
3. WHEN the Agent_Loop requests an image build with a build context path and an image tag, THE Docker_Manager SHALL build a Docker image from the contents of the build context path and assign the specified image tag to the resulting image.
4. WHEN the Agent_Loop requests a container deployment with an image tag, THE Docker_Manager SHALL create and start a container from the image identified by that tag and return the identifier of the created container.
5. IF a Docker operation returns an error, THEN THE Docker_Manager SHALL return the error message produced by Docker, indicate that the operation did not succeed, and leave the targeted container or image in its prior state.
6. IF the Agent_Loop requests a start, stop, restart, or deployment that references a container or image not present on the host, THEN THE Docker_Manager SHALL return an error indicating that the referenced container or image was not found and make no change to any container or image.
7. IF an image build fails because the build context path is invalid or the build process reports an error, THEN THE Docker_Manager SHALL return an error indicating the build failure and SHALL NOT create or tag any image.
8. WHEN the Agent_Loop requests the container list and no Docker containers are present on the host, THE Docker_Manager SHALL return an empty list.

### Requirement 16: Browser Automation

**User Story:** As the Owner, I want the Agent to automate a web browser, so that the Agent can interact with web interfaces and verify web deployments.

#### Acceptance Criteria

1. WHEN the Agent_Loop invokes `browser_open` with a URL, THE Browser_Automation SHALL open the URL in a Playwright-controlled browser page and complete page navigation within 30 seconds.
2. WHEN the Agent_Loop invokes `browser_click` with an element selector, THE Browser_Automation SHALL click the first element matching the selector on the current page within 30 seconds of the element becoming interactable.
3. WHEN the Agent_Loop invokes `browser_type` with an element selector and text of up to 10,000 characters, THE Browser_Automation SHALL enter the text into the first element matching the selector on the current page.
4. WHEN the Agent_Loop invokes `browser_extract_text` with an element selector, THE Browser_Automation SHALL return the text content of the first element matching the selector on the current page.
5. IF the first element matched by `browser_extract_text` contains no text content, THEN THE Browser_Automation SHALL return an empty string.
6. WHEN the Agent_Loop invokes `browser_screenshot`, THE Browser_Automation SHALL capture an image of the currently visible viewport of the current page and return the image file path.
7. IF a Browser_Automation operation fails for any reason, including a selector that matches no element, a navigation or interaction timeout exceeding 30 seconds, or an element that cannot be interacted with, THEN THE Browser_Automation SHALL return an error that describes the failure, identifies the affected selector or URL, and leaves the current page state unchanged.

### Requirement 17: Deployment Management

**User Story:** As the Owner, I want the Agent to build, deploy, and verify applications, so that I can release software from Telegram with confidence.

#### Acceptance Criteria

1. WHEN the Agent_Loop requests a project build, THE Deployment_Manager SHALL run the project build steps and SHALL return a build result indicating success or failure within a maximum of 600 seconds.
2. WHEN the Agent_Loop requests a project upload to a named target, THE Deployment_Manager SHALL transfer the built project artifacts to the named target and SHALL return a transfer result indicating success or failure.
3. WHEN the Agent_Loop requests a project deployment to a named target, THE Deployment_Manager SHALL place the uploaded artifacts into the active location on the named target and SHALL return a deployment result indicating success or failure.
4. WHEN the Agent_Loop requests a service restart on a named target, THE Deployment_Manager SHALL restart the named service on the target and SHALL return a restart result indicating success or failure within a maximum of 120 seconds.
5. WHEN a deployment completes, THE Deployment_Manager SHALL verify the deployment by checking that the deployed application returns a successful response within a maximum of 30 seconds, and SHALL return the verification result.
6. IF the deployment verification check fails, THEN THE Deployment_Manager SHALL return a failure result and the Telegram_Interface SHALL notify the Owner that the deployment verification failed.
7. IF a project build fails or does not complete within 600 seconds, THEN THE Deployment_Manager SHALL return a failure result indicating the build error and the Telegram_Interface SHALL notify the Owner that the build failed.
8. IF an artifact transfer or deployment to a named target fails, THEN THE Deployment_Manager SHALL return a failure result indicating the error, SHALL leave the existing active artifacts on the target unchanged, and the Telegram_Interface SHALL notify the Owner that the operation failed.
9. IF a service restart on a named target fails or does not complete within 120 seconds, THEN THE Deployment_Manager SHALL return a failure result indicating the error and the Telegram_Interface SHALL notify the Owner that the service restart failed.

### Requirement 18: Coding Capabilities

**User Story:** As the Owner, I want the Agent to create, read, modify, and explain code, so that I can develop software through natural-language instructions.

#### Acceptance Criteria

1. WHEN the Owner instructs the Agent to create a new project, THE Agent SHALL create the project directory structure and the initial project files for the requested project through the File_Manager.
2. WHEN the Owner instructs the Agent to create a file or folder, THE Agent SHALL create the requested file or folder through the File_Manager.
3. WHEN the Owner instructs the Agent to edit code in a named file, THE Agent SHALL modify the named file according to the instruction through the File_Manager.
4. WHEN the Owner instructs the Agent to read code in a named file, THE Agent SHALL return the contents of the named file.
5. WHEN the Owner instructs the Agent to refactor code in a named file, THE Agent SHALL produce a revised version of the code in the named file that preserves the behavior described in the instruction through the File_Manager.
6. WHEN the Owner instructs the Agent to fix a bug in a named file, THE Agent SHALL modify the code in the named file to address the described defect through the File_Manager.
7. WHEN the Owner instructs the Agent to run tests for a project, THE Agent SHALL execute the project test command through the Terminal_Executor and return the test results, including the pass or fail outcome of each executed test.
8. WHEN the Owner instructs the Agent to explain code in a named file, THE Agent SHALL return a description of the behavior of the code in the named file.
9. IF the named file does not exist when the Owner instructs the Agent to read, edit, refactor, fix a bug in, or explain code in that file, THEN THE Agent SHALL return an error indicating that the named file was not found and SHALL NOT create or modify any file.
10. IF the requested file or folder already exists when the Owner instructs the Agent to create it, THEN THE Agent SHALL return an error indicating that the file or folder already exists and SHALL NOT overwrite the existing file or folder.
11. IF the project test command cannot be executed, THEN THE Agent SHALL return an error indicating that the tests could not be run.

### Requirement 19: Persistent Memory

**User Story:** As the Owner, I want the Agent to remember conversations, servers, projects, and past tasks, so that the Agent retains context across messages and restarts.

#### Acceptance Criteria

1. THE Memory_Store SHALL persist conversation records, Registered_Server records, project records, and task-history records in a SQLite database.
2. WHEN the Agent processes a message and produces a response, THE Memory_Store SHALL persist the message content and the response content as a single conversation record.
3. WHEN the Agent completes a Task, THE Memory_Store SHALL persist a task-history record that includes the Task description, the executed tools, and the final outcome.
4. WHEN the Agent_Loop requests recent context, THE Memory_Store SHALL return up to the 20 most recent conversation records and up to the 20 most recent task-history records, ordered from most recent to least recent, and SHALL return all available records when fewer than 20 exist.
5. WHEN the Owner sends `/clear`, THE Memory_Store SHALL delete all stored conversation records while retaining the Registered_Server records, project records, and task-history records.
6. WHEN the Agent restarts, THE Memory_Store SHALL load and make available all records persisted before the restart.
7. IF a persistence operation to the SQLite database fails, THEN THE Memory_Store SHALL return an error indication to the Agent and SHALL preserve the previously stored records without partial modification.

### Requirement 20: Activity Logging

**User Story:** As the Owner, I want all Agent activity recorded to log files, so that I can audit what the Agent did.

#### Acceptance Criteria

1. THE Logger SHALL write activity records to files in the `logs/` directory.
2. WHEN the Terminal_Executor or the SSH_Manager runs a command, THE Logger SHALL record the command text and a timestamp.
3. WHEN a command completes, THE Logger SHALL record the command result, including the exit code and the command output, capturing the output up to a maximum of 1,000,000 characters and appending a truncation indication when the output exceeds that limit.
4. WHEN the Agent_Loop selects and executes a tool, THE Logger SHALL record the tool name, the tool arguments, and a timestamp.
5. THE Logger SHALL record each entry with a timestamp and exactly one severity level from the defined set of informational, warning, and error.
6. WHEN the Logger starts and the `logs/` directory does not exist, THE Logger SHALL create the `logs/` directory before writing any activity record.
7. IF the Logger cannot write an activity record to the `logs/` directory because the directory is not writable or storage is unavailable, THEN THE Logger SHALL produce an error indication identifying the failed write and SHALL allow Agent operation to continue without terminating.

### Requirement 21: Credential Security and Destructive-Action Confirmation

**User Story:** As the Owner, I want credentials stored encrypted and destructive actions confirmed, so that my secrets are protected and the Agent does not cause unintended loss.

#### Acceptance Criteria

1. WHEN a credential is stored, THE Security_Manager SHALL encrypt the credential value before the value is written to the Memory_Store.
2. WHEN a stored credential is needed for an operation, THE Security_Manager SHALL decrypt the credential value for use without persisting the decrypted value.
3. WHEN the Agent_Loop selects a Destructive_Action, THE Telegram_Interface SHALL present a description of the Destructive_Action and its target to the Owner and SHALL request an explicit yes-or-no confirmation before execution.
4. IF the Owner does not respond to a Destructive_Action confirmation request within 120 seconds after the request is presented, THEN THE Agent SHALL cancel the Destructive_Action and SHALL report the timeout cancellation to the Owner.
5. IF the Owner's response to a Destructive_Action confirmation request is ambiguous or contradictory, THEN THE Telegram_Interface SHALL re-prompt the Owner for a clear yes-or-no response, up to a maximum of 2 additional attempts.
6. IF the Owner declines a Destructive_Action, THEN THE Agent SHALL cancel the Destructive_Action and SHALL report the cancellation to the Owner.
7. WHEN the Owner confirms a Destructive_Action, THE Agent_Loop SHALL execute the Destructive_Action.
8. IF the Agent_Loop selects an operation that targets a server absent from the Server_Registry, THEN THE Security_Manager SHALL block the operation and return an error that states the server is not authorized.
9. IF the Security_Manager cannot encrypt a credential value, THEN THE Security_Manager SHALL abort storing the credential, SHALL NOT write the unencrypted value to the Memory_Store, and SHALL return an error indication.
10. IF the Security_Manager cannot decrypt a stored credential value, THEN THE Security_Manager SHALL abort the operation that required the credential and SHALL return an error indication.
11. IF the Owner does not provide a clear yes-or-no response after 2 re-prompt attempts, THEN THE Agent SHALL cancel the Destructive_Action and SHALL report the cancellation to the Owner.

### Requirement 22: Project Structure and Technology Stack

**User Story:** As the Owner, I want the Agent delivered as a fully implemented Python project with a defined structure, so that I can deploy and maintain the Agent on my VPS.

#### Acceptance Criteria

1. THE Agent SHALL be organized under an `agent/` package that contains `main.py`, `config.py`, a `database/` module, a `memory/` module, a `telegram/` module, an `llm/` module, and a `tools/` module.
2. THE `tools/` module SHALL contain `ssh.py`, `git.py`, `files.py`, `terminal.py`, `pterodactyl.py`, `cpanel.py`, `docker.py`, and `browser.py`.
3. THE Agent project SHALL include a `logs/` directory, a `requirements.txt` file, and a `README.md` file.
4. THE Agent SHALL be implemented in Python version 3.11 or later.
5. THE Agent SHALL use the OpenAI SDK for the LLM_Client, `python-telegram-bot` for the Telegram_Interface, `paramiko` for the SSH_Manager, `requests` for HTTP API calls, `playwright` for the Browser_Automation, `sqlite3` for the Memory_Store, and `pydantic` for configuration validation.
6. THE Agent SHALL provide a working implementation for every module and tool listed in criteria 1 and 2, with no component left as a placeholder, stub, TODO marker, or pseudocode definition.
7. WHILE running on Python version 3.11 or later with all `requirements.txt` dependencies installed, WHEN the Agent is launched via `main.py`, THE Agent SHALL complete startup and initialize the `database`, `memory`, `telegram`, `llm`, and `tools` modules without raising unhandled exceptions.
8. WHEN the dependencies listed in `requirements.txt` are installed, THE installation SHALL complete with every listed package successfully resolved and no unresolved dependency errors.
9. IF the Agent is launched on a Python runtime earlier than version 3.11, THEN THE Agent SHALL halt startup and produce an error message indicating that the Python version is unsupported.
