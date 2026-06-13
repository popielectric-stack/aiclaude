"""The tool catalog: OpenAI function-calling schemas for every tool (R6.4).

This module defines a single, authoritative description of every action the
model may select across all nine tool components (File_Manager,
Terminal_Executor, Git_Manager, SSH_Manager, Pterodactyl_Manager,
cPanel_Manager, Docker_Manager, Browser_Automation, Deployment_Manager).

Each action is described by a :class:`ToolSpec` carrying:

* ``name`` -- the globally-unique function name the model selects. Method names
  that collide across components (for example ``upload_file`` on SSH,
  Pterodactyl, and cPanel) are namespaced so every catalog entry is unique.
* ``component`` -- the logical component key the Agent_Loop uses to find the
  bound implementation (``"files"``, ``"terminal"``, ``"git"``, ``"ssh"``,
  ``"pterodactyl"``, ``"cpanel"``, ``"docker"``, ``"browser"``,
  ``"deployment"``).
* ``method`` -- the method name to invoke on that component.
* ``description`` -- a concise natural-language summary for the model.
* ``parameters`` -- an OpenAI-style JSON schema for the call arguments.

:func:`build_catalog` renders the specs into the list of OpenAI
function-calling definitions sent to the model. :func:`tool_names` exposes the
set of valid names the ``LLM_Client`` uses to classify a model response as a
valid selection or an ``INVALID_RESPONSE`` (R6.5, R6.6, Property 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ToolSpec:
    """A single catalog entry mapping a model-facing name to an implementation."""

    name: str
    component: str
    method: str
    description: str
    parameters: dict[str, Any]

    def to_openai_function(self) -> dict[str, Any]:
        """Render this spec as an OpenAI function-calling definition (R6.4)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _schema(
    properties: dict[str, dict[str, Any]],
    required: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Build a JSON-schema object for a set of named parameters."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required) if required is not None else list(properties.keys()),
        "additionalProperties": False,
    }


def _str(description: str) -> dict[str, Any]:
    """A string-typed JSON-schema property with a description."""
    return {"type": "string", "description": description}


# The authoritative catalog. Ordering groups entries by component for
# readability; the model receives them all together.
TOOL_SPECS: tuple[ToolSpec, ...] = (
    # -- File_Manager (Requirement 8) ------------------------------------- #
    ToolSpec(
        name="read_file",
        component="files",
        method="read_file",
        description="Read and return the contents of a file at the given path.",
        parameters=_schema({"path": _str("Path of the file to read.")}),
    ),
    ToolSpec(
        name="write_file",
        component="files",
        method="write_file",
        description=(
            "Create or overwrite a file with the given content, creating any "
            "missing parent directories."
        ),
        parameters=_schema(
            {
                "path": _str("Path of the file to write."),
                "content": _str("Full content to write to the file."),
            }
        ),
    ),
    ToolSpec(
        name="append_file",
        component="files",
        method="append_file",
        description="Append content to the end of an existing file, preserving prior content.",
        parameters=_schema(
            {
                "path": _str("Path of the file to append to."),
                "content": _str("Content to append."),
            }
        ),
    ),
    ToolSpec(
        name="delete_file",
        component="files",
        method="delete_file",
        description="Delete the file at the given path.",
        parameters=_schema({"path": _str("Path of the file to delete.")}),
    ),
    ToolSpec(
        name="create_directory",
        component="files",
        method="create_directory",
        description=(
            "Create a directory and any missing parents; succeeds if it already exists."
        ),
        parameters=_schema({"path": _str("Path of the directory to create.")}),
    ),
    ToolSpec(
        name="list_directory",
        component="files",
        method="list_directory",
        description="List the names of entries directly contained in a directory.",
        parameters=_schema({"path": _str("Path of the directory to list.")}),
    ),
    ToolSpec(
        name="search_files",
        component="files",
        method="search_files",
        description=(
            "Recursively search a directory for files whose name or contents "
            "contain the term (case-insensitive)."
        ),
        parameters=_schema(
            {
                "term": _str("Case-insensitive substring to search for."),
                "directory": _str("Directory to search recursively."),
            }
        ),
    ),
    ToolSpec(
        name="create_file",
        component="files",
        method="create_file",
        description=(
            "Create a new file, rejecting the request if the file already exists."
        ),
        parameters=_schema(
            {
                "path": _str("Path of the file to create."),
                "content": _str("Initial content (optional, defaults to empty)."),
            },
            required=["path"],
        ),
    ),
    ToolSpec(
        name="create_folder",
        component="files",
        method="create_folder",
        description=(
            "Create a new folder, rejecting the request if the folder already exists."
        ),
        parameters=_schema({"path": _str("Path of the folder to create.")}),
    ),
    # -- Terminal_Executor (Requirement 9) -------------------------------- #
    ToolSpec(
        name="run_command",
        component="terminal",
        method="run_command",
        description=(
            "Run a shell command on the local VPS and return stdout, stderr, "
            "and the exit code (300s timeout)."
        ),
        parameters=_schema({"command": _str("The shell command to execute.")}),
    ),
    ToolSpec(
        name="get_processes",
        component="terminal",
        method="get_processes",
        description="List running processes with their process id and command name.",
        parameters=_schema({}, required=[]),
    ),
    ToolSpec(
        name="check_disk_usage",
        component="terminal",
        method="check_disk_usage",
        description="Report used and available disk space in MB for each filesystem.",
        parameters=_schema({}, required=[]),
    ),
    ToolSpec(
        name="check_memory_usage",
        component="terminal",
        method="check_memory_usage",
        description="Report used and available memory in MB.",
        parameters=_schema({}, required=[]),
    ),
    ToolSpec(
        name="check_cpu_usage",
        component="terminal",
        method="check_cpu_usage",
        description="Sample CPU utilization over one second and return a percentage 0-100.",
        parameters=_schema({}, required=[]),
    ),
    # -- Git_Manager (Requirement 10) ------------------------------------- #
    ToolSpec(
        name="clone_repo",
        component="git",
        method="clone_repo",
        description="Clone a Git repository URL into a destination path.",
        parameters=_schema(
            {
                "url": _str("Repository URL to clone."),
                "dest": _str("Destination path for the clone."),
            }
        ),
    ),
    ToolSpec(
        name="commit_changes",
        component="git",
        method="commit_changes",
        description="Stage modified files and create a commit with the given message.",
        parameters=_schema(
            {
                "message": _str("Commit message."),
                "repo_path": _str("Repository path (optional, defaults to current directory)."),
            },
            required=["message"],
        ),
    ),
    ToolSpec(
        name="push_changes",
        component="git",
        method="push_changes",
        description="Push a branch to the configured remote.",
        parameters=_schema(
            {
                "branch": _str("Branch name to push."),
                "repo_path": _str("Repository path (optional, defaults to current directory)."),
            },
            required=["branch"],
        ),
    ),
    ToolSpec(
        name="pull_changes",
        component="git",
        method="pull_changes",
        description="Pull a branch from the configured remote.",
        parameters=_schema(
            {
                "branch": _str("Branch name to pull."),
                "repo_path": _str("Repository path (optional, defaults to current directory)."),
            },
            required=["branch"],
        ),
    ),
    ToolSpec(
        name="create_branch",
        component="git",
        method="create_branch",
        description="Create a new Git branch with the given name.",
        parameters=_schema(
            {
                "name": _str("Branch name to create."),
                "repo_path": _str("Repository path (optional, defaults to current directory)."),
            },
            required=["name"],
        ),
    ),
    # -- SSH_Manager (Requirement 12) ------------------------------------- #
    ToolSpec(
        name="ssh_run_command",
        component="ssh",
        method="run_remote_command",
        description="Run a command on a registered remote server over SSH.",
        parameters=_schema(
            {
                "server_name": _str("Name of the registered server."),
                "command": _str("Command to run on the remote server."),
            }
        ),
    ),
    ToolSpec(
        name="ssh_upload_file",
        component="ssh",
        method="upload_file",
        description="Upload a local file to a remote path on a registered server.",
        parameters=_schema(
            {
                "server_name": _str("Name of the registered server."),
                "local_path": _str("Local source file path."),
                "remote_path": _str("Remote destination path."),
            }
        ),
    ),
    ToolSpec(
        name="ssh_download_file",
        component="ssh",
        method="download_file",
        description="Download a remote file from a registered server to a local path.",
        parameters=_schema(
            {
                "server_name": _str("Name of the registered server."),
                "remote_path": _str("Remote source path."),
                "local_path": _str("Local destination path."),
            }
        ),
    ),
    ToolSpec(
        name="ssh_sync_project",
        component="ssh",
        method="sync_project",
        description="Recursively sync a local project directory to a remote directory.",
        parameters=_schema(
            {
                "server_name": _str("Name of the registered server."),
                "local_dir": _str("Local project directory."),
                "remote_dir": _str("Remote destination directory."),
            }
        ),
    ),
    # -- Pterodactyl_Manager (Requirement 13) ----------------------------- #
    ToolSpec(
        name="pterodactyl_list_servers",
        component="pterodactyl",
        method="list_servers",
        description="List Pterodactyl servers available to the API key.",
        parameters=_schema({}, required=[]),
    ),
    ToolSpec(
        name="pterodactyl_power",
        component="pterodactyl",
        method="power",
        description="Send a power signal (start, stop, restart) to a Pterodactyl server.",
        parameters=_schema(
            {
                "server_id": _str("Pterodactyl server identifier."),
                "signal": {
                    "type": "string",
                    "description": "Power signal to send.",
                    "enum": ["start", "stop", "restart", "kill"],
                },
            }
        ),
    ),
    ToolSpec(
        name="pterodactyl_console_output",
        component="pterodactyl",
        method="console_output",
        description="Return the most recent console output (up to 100 lines) of a server.",
        parameters=_schema({"server_id": _str("Pterodactyl server identifier.")}),
    ),
    ToolSpec(
        name="pterodactyl_send_console_command",
        component="pterodactyl",
        method="send_console_command",
        description="Send a console command to a Pterodactyl server.",
        parameters=_schema(
            {
                "server_id": _str("Pterodactyl server identifier."),
                "command": _str("Console command to send."),
            }
        ),
    ),
    ToolSpec(
        name="pterodactyl_upload_file",
        component="pterodactyl",
        method="upload_file",
        description="Upload file content to a path on a Pterodactyl server.",
        parameters=_schema(
            {
                "server_id": _str("Pterodactyl server identifier."),
                "remote_path": _str("Remote destination path."),
                "content": _str("File content to upload."),
            }
        ),
    ),
    ToolSpec(
        name="pterodactyl_download_file",
        component="pterodactyl",
        method="download_file",
        description="Download file content from a path on a Pterodactyl server.",
        parameters=_schema(
            {
                "server_id": _str("Pterodactyl server identifier."),
                "remote_path": _str("Remote source path."),
            }
        ),
    ),
    # -- cPanel_Manager (Requirement 14) ---------------------------------- #
    ToolSpec(
        name="cpanel_upload_file",
        component="cpanel",
        method="upload_file",
        description="Upload a local file (<=100MB) to a cPanel directory.",
        parameters=_schema(
            {
                "local_path": _str("Local source file path."),
                "remote_dir": _str("Remote cPanel directory."),
            }
        ),
    ),
    ToolSpec(
        name="cpanel_download_file",
        component="cpanel",
        method="download_file",
        description="Download a file from a cPanel path.",
        parameters=_schema({"remote_path": _str("Remote cPanel file path.")}),
    ),
    ToolSpec(
        name="cpanel_create_database",
        component="cpanel",
        method="create_database",
        description="Create a database through the cPanel API.",
        parameters=_schema({"name": _str("Database name to create.")}),
    ),
    ToolSpec(
        name="cpanel_create_subdomain",
        component="cpanel",
        method="create_subdomain",
        description="Create a subdomain through the cPanel API.",
        parameters=_schema(
            {
                "subdomain": _str("Subdomain label to create."),
                "domain": _str("Parent domain."),
                "document_root": _str("Document root directory for the subdomain."),
            }
        ),
    ),
    ToolSpec(
        name="cpanel_deploy_website",
        component="cpanel",
        method="deploy_website",
        description="Deploy website files to a cPanel document root.",
        parameters=_schema(
            {
                "local_dir": _str("Local directory of website files."),
                "document_root": _str("Target cPanel document root."),
            }
        ),
    ),
    # -- Docker_Manager (Requirement 15) ---------------------------------- #
    ToolSpec(
        name="docker_list_containers",
        component="docker",
        method="list_containers",
        description="List all Docker containers with id, name, and run state.",
        parameters=_schema({}, required=[]),
    ),
    ToolSpec(
        name="docker_start",
        component="docker",
        method="start",
        description="Start a Docker container by name or id.",
        parameters=_schema({"container": _str("Container name or id.")}),
    ),
    ToolSpec(
        name="docker_stop",
        component="docker",
        method="stop",
        description="Stop a Docker container by name or id (within 10 seconds).",
        parameters=_schema({"container": _str("Container name or id.")}),
    ),
    ToolSpec(
        name="docker_restart",
        component="docker",
        method="restart",
        description="Restart a Docker container by name or id (within 10 seconds).",
        parameters=_schema({"container": _str("Container name or id.")}),
    ),
    ToolSpec(
        name="docker_build_image",
        component="docker",
        method="build_image",
        description="Build a Docker image from a build context and assign a tag.",
        parameters=_schema(
            {
                "context": _str("Build context path."),
                "tag": _str("Image tag to assign."),
            }
        ),
    ),
    ToolSpec(
        name="docker_deploy_container",
        component="docker",
        method="deploy_container",
        description="Create and start a container from an image tag; returns the container id.",
        parameters=_schema(
            {
                "tag": _str("Image tag to deploy."),
                "name": _str("Optional container name."),
            },
            required=["tag"],
        ),
    ),
    # -- Browser_Automation (Requirement 16) ------------------------------ #
    ToolSpec(
        name="browser_open",
        component="browser",
        method="browser_open",
        description="Open a URL in a Playwright-controlled browser page.",
        parameters=_schema({"url": _str("URL to navigate to.")}),
    ),
    ToolSpec(
        name="browser_click",
        component="browser",
        method="browser_click",
        description="Click the first element matching a selector on the current page.",
        parameters=_schema({"selector": _str("CSS/text selector to click.")}),
    ),
    ToolSpec(
        name="browser_type",
        component="browser",
        method="browser_type",
        description="Type text (<=10000 chars) into the element matching a selector.",
        parameters=_schema(
            {
                "selector": _str("Selector of the input element."),
                "text": _str("Text to type."),
            }
        ),
    ),
    ToolSpec(
        name="browser_extract_text",
        component="browser",
        method="browser_extract_text",
        description="Return the text content of the first element matching a selector.",
        parameters=_schema({"selector": _str("Selector of the element to read.")}),
    ),
    ToolSpec(
        name="browser_screenshot",
        component="browser",
        method="browser_screenshot",
        description="Capture a screenshot of the current viewport and return the image path.",
        parameters=_schema({}, required=[]),
    ),
    # -- Deployment_Manager (Requirement 17) ------------------------------ #
    ToolSpec(
        name="deployment_build",
        component="deployment",
        method="build",
        description="Run a project's build command locally (<=600s) and confirm artifacts.",
        parameters=_schema(
            {
                "build_command": _str("Shell command that builds the project."),
                "artifact_path": _str("Path expected to contain build artifacts."),
            }
        ),
    ),
    ToolSpec(
        name="deployment_upload",
        component="deployment",
        method="upload",
        description="Upload built artifacts to a named deployment target server.",
        parameters=_schema(
            {
                "server_name": _str("Registered target server name."),
                "local_path": _str("Local artifact directory."),
                "remote_path": _str("Remote destination directory."),
            }
        ),
    ),
    ToolSpec(
        name="deployment_deploy",
        component="deployment",
        method="deploy",
        description="Run the remote deploy command on a named deployment target.",
        parameters=_schema(
            {
                "server_name": _str("Registered target server name."),
                "local_path": _str("Local artifact directory."),
                "remote_path": _str("Remote destination directory."),
                "deploy_command": _str("Remote command that activates the deployment."),
            },
            required=["server_name", "local_path", "remote_path"],
        ),
    ),
    ToolSpec(
        name="deployment_restart_service",
        component="deployment",
        method="restart_service",
        description="Restart the target service (<=120s) on a named deployment target.",
        parameters=_schema(
            {
                "server_name": _str("Registered target server name."),
                "local_path": _str("Local artifact directory."),
                "remote_path": _str("Remote destination directory."),
                "restart_command": _str("Remote command that restarts the service."),
            },
            required=["server_name", "local_path", "remote_path"],
        ),
    ),
    ToolSpec(
        name="deployment_verify",
        component="deployment",
        method="verify",
        description="Verify the deployed application responds successfully (<=30s).",
        parameters=_schema(
            {
                "server_name": _str("Registered target server name."),
                "local_path": _str("Local artifact directory."),
                "remote_path": _str("Remote destination directory."),
                "verify_url": _str("URL to check for a successful response."),
            },
            required=["server_name", "local_path", "remote_path", "verify_url"],
        ),
    ),
)


# Fail fast at import time if two specs accidentally share a name; the model
# must be able to map a selected name to exactly one implementation.
_seen: set[str] = set()
for _spec in TOOL_SPECS:
    if _spec.name in _seen:
        raise RuntimeError(f"Duplicate tool name in catalog: {_spec.name!r}")
    _seen.add(_spec.name)
del _seen, _spec


_SPECS_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def build_catalog() -> list[dict[str, Any]]:
    """Return the list of OpenAI function-calling definitions (R6.4)."""
    return [spec.to_openai_function() for spec in TOOL_SPECS]


def tool_names() -> frozenset[str]:
    """Return the set of valid tool names for response classification (R6.5)."""
    return frozenset(_SPECS_BY_NAME)


def get_spec(name: str) -> Optional[ToolSpec]:
    """Return the :class:`ToolSpec` for ``name``, or ``None`` if unknown."""
    return _SPECS_BY_NAME.get(name)


# The rendered catalog, computed once for reuse across decisions.
CATALOG: list[dict[str, Any]] = build_catalog()
