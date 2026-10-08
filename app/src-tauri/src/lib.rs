//! Desktop shell: starts the Python engine from the project folder, hands its
//! address to the UI, and stops it when the window closes.

use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

#[derive(Clone, Serialize)]
struct EngineInfo {
    url: String,
    token: String,
}

#[derive(Default)]
struct Engine {
    child: Mutex<Option<Child>>,
    info: Mutex<Option<EngineInfo>>,
    log: Mutex<Option<PathBuf>>,
    error: Mutex<Option<String>>,
}

/// The project folder holds engine/, runtime/, models/ and data/. Walk up from
/// the executable so a release build inside app/src-tauri/target still finds it.
fn project_root() -> PathBuf {
    if let Ok(path) = std::env::var("DFORGE_ROOT") {
        return PathBuf::from(path);
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent();
        while let Some(d) = dir {
            if d.join("engine").join("dforge").is_dir() {
                return d.to_path_buf();
            }
            dir = d.parent();
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn python(root: &Path) -> Option<PathBuf> {
    let candidates = if cfg!(windows) {
        vec![root.join("runtime").join("python").join("python.exe")]
    } else {
        vec![
            root.join("runtime").join("python").join("bin").join("python3"),
            PathBuf::from("/usr/bin/python3"),
        ]
    };
    candidates.into_iter().find(|p| p.is_file())
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(7863)
}

fn start_engine(engine: &Engine) {
    let root = project_root();
    let Some(python) = python(&root) else {
        *engine.error.lock().unwrap() = Some(format!(
            "Portable Python not found in {}. Run scripts\\setup.ps1.",
            root.join("runtime").join("python").display()
        ));
        return;
    };
    let logs = root.join("data").join("logs");
    let _ = fs::create_dir_all(&logs);
    let log_path = logs.join("engine.log");
    let log = File::create(&log_path).ok();
    let port = free_port();
    let token = uuid::Uuid::new_v4().simple().to_string();
    let mut command = Command::new(&python);
    command
        .args(["-s", "-m", "dforge", "serve", "--port"])
        .arg(port.to_string())
        .args(["--token", &token, "--parent-pid"])
        .arg(std::process::id().to_string())
        .current_dir(root.join("engine"))
        .env("DFORGE_ROOT", &root)
        .env("PYTHONPATH", root.join("engine"))
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null());
    if let Some(file) = log {
        if let Ok(copy) = file.try_clone() {
            command.stdout(Stdio::from(copy));
        }
        command.stderr(Stdio::from(file));
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    match command.spawn() {
        Ok(child) => {
            *engine.child.lock().unwrap() = Some(child);
            *engine.info.lock().unwrap() = Some(EngineInfo {
                url: format!("http://127.0.0.1:{port}"),
                token,
            });
        }
        Err(err) => {
            *engine.error.lock().unwrap() = Some(format!("Could not start {}: {err}", python.display()));
        }
    }
    *engine.log.lock().unwrap() = Some(log_path);
}

fn stop_engine(engine: &Engine) {
    if let Some(mut child) = engine.child.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[tauri::command]
fn engine_info(engine: State<Engine>) -> Option<EngineInfo> {
    engine.info.lock().unwrap().clone()
}

#[tauri::command]
fn engine_log(engine: State<Engine>) -> String {
    if let Some(err) = engine.error.lock().unwrap().clone() {
        return err;
    }
    let Some(path) = engine.log.lock().unwrap().clone() else {
        return String::new();
    };
    let mut text = String::new();
    if let Ok(mut file) = File::open(path) {
        let len = file.metadata().map(|m| m.len()).unwrap_or(0);
        let _ = file.seek(SeekFrom::Start(len.saturating_sub(4000)));
        let _ = file.read_to_string(&mut text);
    }
    text
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Engine::default())
        .setup(|app| {
            let engine = app.state::<Engine>();
            start_engine(&engine);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_info, engine_log])
        .build(tauri::generate_context!())
        .expect("failed to build Digital Forge 3D Slicer");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_engine(&handle.state::<Engine>());
        }
    });
}
