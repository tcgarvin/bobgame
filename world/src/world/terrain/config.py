"""Terrain generation configuration models."""

from pydantic import BaseModel, Field


class NoiseConfig(BaseModel):
    """Noise generation parameters for a single field."""

    base_wavelength: int = Field(default=900, description="Base wavelength in tiles")
    octaves: int = Field(default=6, description="Number of octaves for fBm")
    lacunarity: float = Field(
        default=2.0, description="Frequency multiplier per octave"
    )
    gain: float = Field(default=0.5, description="Amplitude multiplier per octave")


class WarpConfig(BaseModel):
    """Domain warping configuration."""

    amplitude: int = Field(default=120, description="Warp amplitude in tiles")
    wavelength: int = Field(default=700, description="Warp noise wavelength")
    octaves: int = Field(default=3, description="Warp noise octaves")


class ElevationConfig(BaseModel):
    """Elevation field generation parameters."""

    noise: NoiseConfig = Field(default_factory=NoiseConfig)
    ridged_wavelength: int = Field(default=1100, description="Ridged noise wavelength")
    ridged_octaves: int = Field(default=4, description="Ridged noise octaves")
    ridged_weight: float = Field(default=0.55, description="Ridged contribution weight")
    warp: WarpConfig = Field(default_factory=WarpConfig)


class MoistureConfig(BaseModel):
    """Moisture field generation parameters."""

    noise: NoiseConfig = Field(
        default_factory=lambda: NoiseConfig(base_wavelength=1200, octaves=5, gain=0.55)
    )
    water_influence: float = Field(
        default=0.35, description="Weight of distance-to-water on moisture"
    )


class IslandConfig(BaseModel):
    """Island shaping parameters."""

    land_fraction: float = Field(default=0.70, description="Target land fraction (0-1)")
    border_width: int = Field(default=3, description="Guaranteed water border width")
    falloff_start: float = Field(
        default=0.62, description="Normalized radius where falloff begins"
    )
    falloff_end: float = Field(
        default=0.98, description="Normalized radius where falloff reaches max"
    )
    coast_drop: float = Field(default=2.2, description="Elevation drop at falloff end")


class HydrologyConfig(BaseModel):
    """River and water feature parameters."""

    river_count_min: int = Field(default=6, description="Minimum number of rivers")
    river_count_max: int = Field(default=9, description="Maximum number of rivers")
    river_width_min: int = Field(default=2, description="Minimum river width in tiles")
    river_width_max: int = Field(default=3, description="Maximum river width in tiles")
    fords_per_river_min: int = Field(default=1, description="Min fords per river")
    fords_per_river_max: int = Field(default=3, description="Max fords per river")
    ford_length: int = Field(default=8, description="Ford length in tiles")
    source_min_distance_from_coast: int = Field(
        default=150, description="Min distance from coast for river sources"
    )
    source_min_spacing: int = Field(
        default=400, description="Min spacing between river sources"
    )
    meander_temperature: float = Field(
        default=0.1, description="Temperature for stochastic meander (higher=more)"
    )


class ClassificationConfig(BaseModel):
    """Terrain classification thresholds."""

    beach_max_width: int = Field(default=3, description="Maximum beach width")
    mountain_fraction_max: float = Field(
        default=0.10, description="Max fraction of land as mountains"
    )
    mountain_distance_from_water: int = Field(
        default=20, description="Min distance from water for mountains"
    )
    mountain_elevation_quantile: float = Field(
        default=0.88, description="Elevation quantile threshold for mountains"
    )
    moisture_dirt_threshold: float = Field(
        default=0.42, description="Moisture below this becomes dirt"
    )
    shallow_water_max_depth: int = Field(
        default=2, description="Max distance from land for shallow water"
    )
    shallow_water_noise_threshold: float = Field(
        default=0.55, description="Noise threshold for shallow patches"
    )


class ForestConfig(BaseModel):
    """Forest/vegetation parameters."""

    base_wavelength: int = Field(
        default=600, description="Forest density noise wavelength"
    )
    octaves: int = Field(default=4, description="Forest density noise octaves")
    smoothstep_low: float = Field(default=0.2, description="Smoothstep lower bound")
    smoothstep_high: float = Field(default=0.75, description="Smoothstep upper bound")


class ObjectPlacementConfig(BaseModel):
    """Natural object placement parameters."""

    # Trees: probability under full canopy, plus a thin scatter in the open.
    tree_base_density: float = Field(
        default=0.16, description="Tree probability under full canopy"
    )
    tree_stray_density: float = Field(
        default=0.004, description="Lone-tree probability in open country"
    )
    tree_coast_distance: int = Field(
        default=40, description="Distance from the ocean for full tree density"
    )
    grove_wavelength: int = Field(
        default=90, description="Wavelength of the grove/clearing noise"
    )
    canopy_threshold: float = Field(
        default=0.56, description="Forest+grove level above which canopy closes"
    )
    canopy_edge_softness: float = Field(
        default=0.06, description="Half-width of the canopy edge transition"
    )

    # Bushes: thickets along forest edges, in a band back from the water.
    bush_base_density: float = Field(
        default=0.05, description="Bush probability in a thicket or forest edge"
    )
    bush_water_min_distance: int = Field(
        default=4, description="Min distance from water for bushes"
    )
    bush_water_max_distance: int = Field(
        default=60, description="Distance from water beyond which bushes thin out"
    )

    # Rocks: outcrops and mountain-foot scree, plus strays.
    rock_base_density: float = Field(
        default=0.18, description="Rock probability inside an outcrop"
    )
    rock_stray_density: float = Field(
        default=0.0015, description="Stray rock probability anywhere"
    )
    outcrop_wavelength: int = Field(
        default=55, description="Wavelength of the outcrop patch noise"
    )
    outcrop_threshold: float = Field(
        default=0.76, description="Rockiness above which an outcrop forms"
    )
    rock_mountain_reach: int = Field(
        default=14, description="How far scree spreads from mountain feet"
    )

    # Reeds: fresh-water banks and ford shallows.
    reed_base_density: float = Field(
        default=0.45, description="Reed probability inside a reed bed"
    )
    reed_bank_width: float = Field(
        default=2.0, description="Max distance from a river for bank reeds"
    )
    reed_wavelength: int = Field(
        default=45, description="Wavelength of the reed-bed patch noise"
    )
    reed_coast_exclusion: int = Field(
        default=12, description="No reeds this close to the ocean"
    )

    # Clay: tight clusters a little back from the river bank.
    clay_base_density: float = Field(
        default=0.22, description="Clay probability inside a clay pit patch"
    )
    clay_min_distance: float = Field(
        default=2.0, description="Min distance from a river for clay"
    )
    clay_max_distance: float = Field(
        default=7.0, description="Max distance from a river for clay"
    )
    clay_wavelength: int = Field(
        default=30, description="Wavelength of the clay patch noise"
    )
    forest: ForestConfig = Field(default_factory=ForestConfig)


class TerrainConfig(BaseModel):
    """Complete terrain generation configuration."""

    seed: int = Field(default=42, description="Random seed for reproducibility")
    width: int = Field(default=4000, description="World width in tiles")
    height: int = Field(default=4000, description="World height in tiles")

    elevation: ElevationConfig = Field(default_factory=ElevationConfig)
    moisture: MoistureConfig = Field(default_factory=MoistureConfig)
    island: IslandConfig = Field(default_factory=IslandConfig)
    hydrology: HydrologyConfig = Field(default_factory=HydrologyConfig)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    objects: ObjectPlacementConfig = Field(default_factory=ObjectPlacementConfig)

    # Debug options
    debug_output_dir: str | None = Field(
        default=None, description="Directory for debug images (None = disabled)"
    )
