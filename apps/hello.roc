app [main!] { pf: platform "../platform/main.roc" }

import pf.Host

main! : {} => {}
main! = |{}| Host.puts!("Hello, World!\n")
