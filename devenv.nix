{pkgs, ...}: {
  packages = [pkgs.stdenv.cc.cc.lib];

  enterShell = ''
    export LD_LIBRARY_PATH="$LD_LIBRARY_PATH:${pkgs.stdenv.cc.cc.lib}/lib"
  '';
}
