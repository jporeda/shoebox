# ExifTool user-defined XMP namespace for Shoebox provenance.
# Lets exported copies and sidecars be matched back to the source photo.
%Image::ExifTool::UserDefined = (
    'Image::ExifTool::XMP::Main' => {
        shoebox => {
            SubDirectory => {
                TagTable => 'Image::ExifTool::UserDefined::shoebox',
            },
        },
    },
);

%Image::ExifTool::UserDefined::shoebox = (
    GROUPS        => { 0 => 'XMP', 1 => 'XMP-shoebox', 2 => 'Image' },
    NAMESPACE     => { 'shoebox' => 'https://github.com/jporeda/shoebox/ns/1.0/' },
    WRITABLE      => 'string',
    PhotoID       => { },
    OriginalSHA256 => { },
    ImageDataHash => { },
    OriginalPath  => { },
    DateSource    => { },
    DatePrecision => { },
    DateConfidence => { Writable => 'real' },
    RunID         => { },
    RotationBaked => { Writable => 'boolean' },
);

1;  # end
