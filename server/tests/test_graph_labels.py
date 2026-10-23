from dep_intel.graph_labels import annotate_flow_paths, node_label, relative_repo_path


def test_relative_repo_path_strips_checkout_root():
    assert relative_repo_path("/work/auth-service/src/server.js", "/work/auth-service") == "src/server.js"
    assert relative_repo_path("src/server.js", "/work/auth-service") == "src/server.js"


def test_flow_path_ids_become_file_labels():
    payload = {
        "flows": [
            {
                "name": "validate",
                "path": [4, 9],
                "file": "/work/auth/src/server.js",
                "files": ["/work/auth/src/server.js", "/work/auth/src/users.js"],
            }
        ]
    }
    labels = {
        4: node_label("validate", "/work/auth/src/server.js", "/work/auth"),
        9: node_label("listUsers", "/work/auth/src/users.js", "/work/auth"),
    }
    annotate_flow_paths(payload, labels, "/work/auth")
    flow = payload["flows"][0]
    assert flow["path_files"] == ["src/server.js (validate)", "src/users.js (listUsers)"]
    assert flow["file"] == "src/server.js"
    assert flow["files"] == ["src/server.js", "src/users.js"]
    assert flow["path"] == [4, 9]
