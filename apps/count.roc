app [main!] { pf: platform "../platform/main.roc" }

import pf.Host

## Triangle numbers, printed digit by digit: arithmetic and a loop in Roc,
## one byte at a time through the host.
main! : {} => {}
main! = |{}| {
    var $total = 0.U16
    for n in 1..<11.U16 {
        $total = $total + n
        print_u16!($total)
        Host.putc!(' ')
    }
    Host.puts!("\n")
}

print_u16! : U16 => {}
print_u16! = |value| {
    if value >= 10 {
        print_u16!(value // 10)
    }
    Host.putc!(48 + (value % 10).to_u8_wrap())
}
