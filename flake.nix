{
  description = "Local TDX strategy replay and QMT migration preparation";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  outputs = { self, nixpkgs }: let
    system = "x86_64-linux";
    pkgs = import nixpkgs { inherit system; };
    python = pkgs.python3.withPackages (ps: [ ps.numpy ps.pandas ps.pyarrow ps.pytest ]);
  in {
    devShells.${system}.default = pkgs.mkShell {
      packages = [ python pkgs.vermin ];
      OPENBLAS_NUM_THREADS = "1";
      OMP_NUM_THREADS = "1";
      MKL_NUM_THREADS = "1";
      # The vermin package hook rewrites PYTHONPATH; keep the repo importable.
      shellHook = ''
        export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"
      '';
    };
  };
}
