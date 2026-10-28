app [main!] { pf: platform "../platform/main.roc" }

import pf.Host

## Rabinowitz and Wagon's spigot for pi: the first 69 digits, one at a time,
## from integer arithmetic alone. Every term fits in a U16: for 69 digits the
## array is 230 long, and 10 * a[i] + q * i stays under 10,000.
digits : U16
digits = 69

main! : {} => {}
main! = |{}| {
    len = 10 * digits // 3
    var $a = List.repeat(2.U16, len.to_u64())
    var $nines = 0.U16
    var $predigit = 0.U16
    var $first = True
    Host.puts!("Calculating first ")
    print_u16!(digits)
    Host.puts!(" digits of pi: ")
    for _ in 0..<digits {
        # Multiply by 10 and put the array into regular form, right to left.
        var $q = 0.U16
        var $i = len
        while $i > 0 {
            index = ($i - 1).to_u64()
            denominator = 2 * $i - 1
            x = 10 * ($a.get(index) ?? 0) + $q * $i
            $a = $a.set(index, x % denominator) ?? $a
            $q = x // denominator
            $i = $i - 1
        }
        $a = $a.set(0, $q % 10) ?? $a
        $q = $q // 10

        # Hold 9s back until the next predigit says whether they carry.
        if $q == 9 {
            $nines = $nines + 1
        } else if $q == 10 {
            print_digit!($predigit + 1)
            repeat_digit!(0, $nines)
            $predigit = 0
            $nines = 0
        } else {
            # The very first predigit is the 0 before the 3: skip it.
            if !$first {
                print_digit!($predigit)
            }
            $first = False
            $predigit = $q
            repeat_digit!(9, $nines)
            $nines = 0
        }
    }
    print_digit!($predigit)
    Host.puts!("\n")
}

print_digit! : U16 => {}
print_digit! = |digit| Host.putc!(48 + digit.to_u8_wrap())

repeat_digit! : U16, U16 => {}
repeat_digit! = |digit, count| {
    for _ in 0..<count {
        print_digit!(digit)
    }
}

print_u16! : U16 => {}
print_u16! = |value| {
    if value >= 10 {
        print_u16!(value // 10)
    }
    print_digit!(value % 10)
}
