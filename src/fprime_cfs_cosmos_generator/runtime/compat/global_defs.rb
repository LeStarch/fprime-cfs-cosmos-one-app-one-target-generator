# Generated F Prime cFS compatibility helper for nasa/cfs-cosmos-plugin. Do not edit.
#
# The generated cmd/tlm definitions call Globals::Cfg.processor_endian; nasa/cfs-cosmos-plugin configures the
# processor byte order through cfs_globals.rb ($cfs_globals_endianness, set from the cfs_endianness VARIABLE in
# plugin.txt). This file maps one onto the other. It is only evaluated while the plugin's cmd/tlm ERB renders,
# where every targets/*/lib directory is on the load path; it must not be required from runtime code.
begin
  require 'cfs_globals'
rescue LoadError
  # plugin.txt requires cfs_globals before any target renders; without it processor_endian raises below.
end

module Globals
  module Cfg
    class EndiannessNotConfigured < StandardError; end

    def self.processor_endian
      value = $cfs_globals_endianness if defined?($cfs_globals_endianness)
      if value.nil?
        raise EndiannessNotConfigured,
              'global_defs.rb: $cfs_globals_endianness is unset; require cfs_globals (nasa/cfs-cosmos-plugin) first'
      end
      value
    end
  end
end
