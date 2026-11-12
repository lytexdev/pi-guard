//! For the phone app. The app's own connection runs through the tunnel like any other, so this
//! only needs the Pi's address in it. Nothing may escape into the app: no panic, no exception.

use crate::{device, KEY_SIZE, PORT, SERVER};
use jni::objects::{JByteArray, JClass};
use jni::sys::jbyteArray;
use jni::JNIEnv;
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::panic::catch_unwind;
use std::time::Duration;

const WAIT: Duration = Duration::from_secs(8);

fn exchange(start: &[u8; KEY_SIZE]) -> Option<crate::Key> {
    let address = SocketAddr::new(SERVER.parse::<IpAddr>().ok()?, PORT);
    let mut stream = TcpStream::connect_timeout(&address, WAIT).ok()?;
    stream.set_read_timeout(Some(WAIT)).ok()?;
    stream.set_write_timeout(Some(WAIT)).ok()?;
    device(&mut stream, start).ok()
}

/// `de.lytex.Kem.exchange(startingKey)`: the new key, or null if there is none.
#[no_mangle]
pub extern "system" fn Java_de_lytex_Kem_exchange<'local>(
    env: JNIEnv<'local>,
    _class: JClass<'local>,
    start: JByteArray<'local>,
) -> jbyteArray {
    let Ok(start) = env.convert_byte_array(&start) else { return std::ptr::null_mut() };
    let Ok(start) = <[u8; KEY_SIZE]>::try_from(start.as_slice()) else { return std::ptr::null_mut() };
    match catch_unwind(|| exchange(&start)) {
        Ok(Some(key)) => env.byte_array_from_slice(key.as_slice()).map_or(std::ptr::null_mut(), |a| a.into_raw()),
        _ => std::ptr::null_mut(),
    }
}
