//! How long one exchange takes on this machine.
use pi_guard_kem::{device, pi};
use std::net::{TcpListener, TcpStream};
use std::time::Instant;

fn main() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let _ = pi(&mut stream.unwrap(), &[7; 32]);
        }
    });
    for round in 1..=5 {
        let begin = Instant::now();
        let mut stream = TcpStream::connect(address).unwrap();
        device(&mut stream, &[7; 32]).unwrap();
        println!("exchange {round}: {} ms", begin.elapsed().as_millis());
    }
}
