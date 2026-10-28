platform ""
    requires {
        main! : {} => {}
    }
    exposes [Host]
    packages {}
    provides { "roc_main": main_for_host! }
    hosted {
        "roc_host_puts": Host.puts!,
        "roc_host_putc": Host.putc!,
    }
    # Only the bitcode is used: build.sh retargets it to the 6502.
    targets: {
        inputs_dir: "targets/",
        wasm32: { inputs: [app], output: Archive },
    }

import Host

main_for_host! : {} => {}
main_for_host! = |{}| main!({})
