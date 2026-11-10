//! A small JSON file that pi-guard reads for its dashboard. Written whole, then renamed, so a
//! reader never sees half of it. It holds no tokens.

use std::path::Path;

pub fn write(path: &Path, value: &serde_json::Value) {
    let Ok(text) = serde_json::to_vec_pretty(value) else {
        return;
    };
    let temporary = path.with_extension("tmp");
    if std::fs::write(&temporary, text).is_ok() {
        let _ = std::fs::rename(&temporary, path);
    }
}
