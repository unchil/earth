/**
 * products - defines the behavior of weather data grids, including grid construction, interpolation, and color scales.
 *
 * Copyright (c) 2014 Cameron Beccario
 * The MIT License - http://opensource.org/licenses/MIT
 *
 * https://github.com/cambecc/earth
 */
var products = function() {
    "use strict";
    var ENDPOINT = "http://192.168.55.6:7788/earth/env";

    function buildProduct(overrides) {
        return _.extend({
            description: "",
            paths: [],
            date: null,
            navigate: function(step) {
                return gfsStep(this.date, step);
            },
            load: function(cancel) {
                var me = this;
                return when.map(this.paths, µ.loadJson).then(function(files) {
                    return cancel.requested ? null : _.extend(me, buildGrid(me.builder.apply(me, files)));
                });
            }
        }, overrides);
    }


    function earthDataServiceUrl(attr, type){
        var mode =  attr.param === 'wind' ? 'air' : attr.param === 'ocean' ? 'ocean' : 'ice';
        var stamp, level, surface , url, prefix;

        if( mode === 'air'){
            stamp = attr.date === "current" ? "current" : attr.date.replace(/\//g, "") + attr.hour.slice(0, 2);
            surface = attr.surface === "surface" ? "isobaric" : attr.surface;
            level = attr.level === "level" ? "1000hPa" : attr.level;
            url = `${ENDPOINT}?time=${stamp}&mode=${mode}&type=${type}&surface=${surface}&level=${level}`;
            return url;
        }else{
            prefix = µ.ymdRedelimit(attr.date, "/", "", attr.param);
            stamp = attr.date === "current" ? "current" : prefix + attr.hour.slice(0, 2);
            surface = "surface";
            level = "level";
            url = `${ENDPOINT}?time=${stamp}&mode=${mode}&type=${type}&surface=${surface}&level=${level}`;
            return url;
        }
    }

    function gfsDate(attr) {
        if (attr.date === "current") {
            // Construct the date from the current time, rounding down to the nearest three-hour block.
            var now = new Date(Date.now()), hour = Math.floor(now.getUTCHours() / 3);
            return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour));
        }
        var parts = attr.date.split("/");
        return new Date(Date.UTC(+parts[0], parts[1] - 1, +parts[2], +attr.hour.substring(0, 2)));
    }

    /**
     * Returns a date for the chronologically next or previous GFS data layer. How far forward or backward in time
     * to jump is determined by the step. Steps of ±1 move in 3-hour jumps, and steps of ±10 move in 24-hour jumps.
     */
    function gfsStep(date, step) {
        var offset = (step > 1 ? 8 : step < -1 ? -8 : step) * 3, adjusted = new Date(date);
        adjusted.setHours(adjusted.getHours() + offset);
        return adjusted;
    }

    function netcdfHeader(time, lat, lon, center) {
        return {
            lo1: lon.sequence.start,
            la1: lat.sequence.start,
            dx: lon.sequence.delta,
            dy: -lat.sequence.delta,
            nx: lon.sequence.size,
            ny: lat.sequence.size,
            refTime: time.data[0],
            forecastTime: 0,
            centerName: center
        };
    }

    function describeSurface(attr) {
        return attr.surface === "surface" ? "Surface" : µ.capitalize(attr.level);
    }

    function describeSurfaceJa(attr) {
        return attr.surface === "surface" ? "地上" : µ.capitalize(attr.level);
    }

    /**
     * Returns a function f(langCode) that, given table:
     *     {foo: {en: "A", ja: "あ"}, bar: {en: "I", ja: "い"}}
     * will return the following when called with "en":
     *     {foo: "A", bar: "I"}
     * or when called with "ja":
     *     {foo: "あ", bar: "い"}
     */
    function localize(table) {
        return function(langCode) {
            var result = {};
            _.each(table, function(value, key) {
                result[key] = value[langCode] || value.en || value;
            });
            return result;
        }
    }

    // wind 를 제외한 다른 overlayType 은 default 인 wind를 (matches: _.matches({param: "wind"}),)호출한후 호출한다.
    // 그래서 earthDataServiceUrl 호출시 반드시 overlayType 을 넘겨야 한다.
    var FACTORIES = {

        "wind": {
            matches: _.matches({param: "wind"}),
            create: function(attr) {
                return buildProduct({
                    field: "vector",
                    type: "wind",
                    description: localize({
                        name: {en: "Wind"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                  paths: [ earthDataServiceUrl(attr, 'wind') ],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var uData = file[0].data, vData = file[1].data;
                        return {
                            header: file[0].header,
                            interpolate: bilinearInterpolateVector,
                            data: function(i) {
                                return [uData[i], vData[i]];
                            }
                        }
                    },
                    units: [
                        {label: "km/h", conversion: function(x) { return x * 3.6; },      precision: 0},
                        {label: "m/s",  conversion: function(x) { return x; },            precision: 1},
                        {label: "kn",   conversion: function(x) { return x * 1.943844; }, precision: 0},
                        {label: "mph",  conversion: function(x) { return x * 2.236936; }, precision: 0}
                    ],
                    scale: {
                        bounds: [0, 100],
                        /*
                        gradient: µ.segmentedColorScale([
                            [0,   [22, 36, 92]],     //  0: 정온/극약풍 (딥 인디고-블루)
                            [10,  [25, 110, 225]],   // 10: 약한 바람 (로열 블루)
                            [20,  [24, 195, 215]],   // 20: 완만한 바람 (시안)
                            [35,  [65, 215, 130]],   // 35: 보통~신선한 바람 (에메랄드 그린)
                            [50,  [245, 215, 45]],   // 50: 강한 바람 (옐로우)
                            [68,  [245, 125, 30]],   // 68: 매우 강한 바람 (오렌지)
                            [85,  [225, 40, 55]],    // 85: 폭풍/강한 제트 (레드)
                            [100, [165, 20, 95]]     // 100: 최대 상한 (크림슨/마젠타)
                        ])

                         */
                        gradient: µ.segmentedColorScale([
                            [0,   [18, 34, 88]],
                            [8,   [22, 95, 220]],
                            [16,  [25, 185, 215]],
                            [28,  [55, 215, 135]],
                            [42,  [175, 225, 65]],
                            [58,  [248, 195, 40]],
                            [78,  [242, 95, 30]],
                            [100, [205, 30, 75]]
                        ])
                    },
                    particles: {velocityScale: 1/60000, maxIntensity: 17}
                });
            }
        } ,
        "temperature": {
            matches: _.matches({param: "wind", overlayType: "temperature"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "temperature",
                    description: localize({
                        name: {en: "Temp"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [ earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "°C", conversion: function(x) { return x - 273.15; },       precision: 1},
                        {label: "°F", conversion: function(x) { return x * 9/5 - 459.67; }, precision: 1},
                        {label: "K",  conversion: function(x) { return x; },                precision: 1}
                    ],
                    scale: {
                        bounds: [193, 328],
                        gradient: µ.segmentedColorScale([
                            [193.0,  [42, 12, 88]],     // 193.0 K (-80.15°C): 극한 한랭 상층/극지방 (딥 퍼플-인디고)
                            [215.0,  [55, 35, 165]],    // 215.0 K (-58.15°C): 강추위 (바이올렛 블루)
                            [233.15, [35, 110, 220]],   // 233.15 K (-40.0°C): -40°C 기준 (로열 블루)
                            [255.37, [25, 185, 220]],   // 255.37 K (-17.78°C / 0°F): 한랭 공기 (시안)
                            [273.15, [45, 210, 145]],   // 273.15 K (0.0°C): 결빙/빙점 기준선 (에메랄드/청록)
                            [285.15, [130, 220, 75]],   // 285.15 K (12.0°C): 서늘~온대 (옐로우 그린)
                            [293.15, [245, 215, 45]],   // 293.15 K (20.0°C): 온화한 기온 (옐로우)
                            [303.15, [245, 130, 30]],   // 303.15 K (30.0°C): 더운 기온/여름 (오렌지)
                            [315.15, [225, 40, 50]],    // 315.15 K (42.0°C): 폭염 (레드)
                            [328.0,  [135, 20, 75]]     // 328.0 K (54.85°C): 최고 상한 (다크 크림슨/마젠타)
                        ])

                    }
                });
            }
        },

        "relative_humidity": {
            matches: _.matches({param: "wind", overlayType: "relative_humidity"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "relative_humidity",
                    description: localize({
                        name: {en: "Relative Humidity"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        };
                    },
                    units: [
                        {label: "%", conversion: function(x) { return x; }, precision: 0}
                    ],
                    scale: {
                        bounds: [0, 100],
                        gradient: µ.segmentedColorScale([
                            [0,   [195, 110, 45]],   //  0%: 매우 건조 (웜 브라운/앰버)
                            [20,  [235, 155, 45]],   // 20%: 건조 (오렌지)
                            [40,  [240, 215, 65]],   // 40%: 다소 건조~보통 (옐로우)
                            [60,  [95, 215, 130]],   // 60%: 보통~다습 진입 (에메랄드/그린)
                            [75,  [35, 195, 220]],   // 75%: 다습 (시안)
                            [85,  [30, 125, 230]],   // 85%: 고습 (스카이/로열 블루)
                            [95,  [45, 65, 200]],    // 95%: 포화 근접 (딥 블루)
                            [100, [75, 35, 165]]     // 100%: 최대 포화 (인디고/바이올렛)
                        ])
                    }
                });
            }
        },
/*
        "ice_concentration": {
            matches: _.matches({param: "wind", overlayType: "ice_concentration"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "ice_concentration",
                    description: localize({
                        name: {en: "Ice Concentration"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        };
                    },
                    units: [
                        {label: "%", conversion: function(x) { return x; }, precision: 0}
                    ],
                    scale: {
                        bounds: [0, 1],
                        gradient: function(v, a) {
                            return µ.sinebowColor(Math.min(v, 1) / 1, a);
                        }
                    }
                });
            }
        },
*/
        /*

        "direction_of_wind_wave": {
            matches: _.matches({param: "wind", overlayType: "direction_of_wind_wave"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "direction_of_wind_wave",
                    description: localize({
                        name: {en: "Direction Of Wind Wave", ja: ""},
                        qualifier: {en: " @ " + describeSurface(attr), ja: " @ " + describeSurfaceJa(attr)}
                    }),
                    paths: [gfs1p0degPath(attr, "direction_of_wind_wave", attr.surface, attr.level)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        };
                    },
                    units: [
                        {label: "degrees", conversion: function(x) { return x; }, precision: 2}
                    ],
                    scale: {
                        bounds: [0, 360.0],
                        gradient: function(v, a) {
                            return µ.sinebowColor(Math.min(v, 1.5) / 1.5, a);
                        }
                    }
                });
            }
        },

        "air_density": {
            matches: _.matches({param: "wind", overlayType: "air_density"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "air_density",
                    description: localize({
                        name: {en: "Air Density", ja: "空気密度"},
                        qualifier: {en: " @ " + describeSurface(attr), ja: " @ " + describeSurfaceJa(attr)}
                    }),
                    paths: [gfs1p0degPath(attr, "air_density", attr.surface, attr.level)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var vars = file.variables;
                        var air_density = vars.air_density, data = air_density.data;
                        return {
                            header: netcdfHeader(vars.time, vars.lat, vars.lon, file.Originating_or_generating_Center),
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        };
                    },
                    units: [
                        {label: "kg/m³", conversion: function(x) { return x; }, precision: 2}
                    ],
                    scale: {
                        bounds: [0, 1.5],
                        gradient: function(v, a) {
                            return µ.sinebowColor(Math.min(v, 1.5) / 1.5, a);
                        }
                    }
                });
            }
        },

        "wind_power_density": {
            matches: _.matches({param: "wind", overlayType: "wind_power_density"}),
            create: function(attr) {
                var windProduct = FACTORIES.wind.create(attr);
                var airdensProduct = FACTORIES.air_density.create(attr);
                return buildProduct({
                    field: "scalar",
                    type: "wind_power_density",
                    description: localize({
                        name: {en: "Wind Power Density", ja: "風力エネルギー密度"},
                        qualifier: {en: " @ " + describeSurface(attr), ja: " @ " + describeSurfaceJa(attr)}
                    }),
                    paths: [windProduct.paths[0], airdensProduct.paths[0]],
                    date: gfsDate(attr),
                    builder: function(windFile, airdensFile) {
                        var windBuilder = windProduct.builder(windFile);
                        var airdensBuilder = airdensProduct.builder(airdensFile);
                        var windData = windBuilder.data, windInterpolate = windBuilder.interpolate;
                        var airdensData = airdensBuilder.data, airdensInterpolate = airdensBuilder.interpolate;
                        return {
                            header: _.clone(airdensBuilder.header),
                            interpolate: function(x, y, g00, g10, g01, g11) {
                                var m = windInterpolate(x, y, g00[0], g10[0], g01[0], g11[0])[2];
                                var ρ = airdensInterpolate(x, y, g00[1], g10[1], g01[1], g11[1]);
                                return 0.5 * ρ * m * m * m;
                            },
                            data: function(i) {
                                return [windData(i), airdensData(i)];
                            }
                        };
                    },
                    units: [
                        {label: "kW/m²", conversion: function(x) { return x / 1000; }, precision: 1},
                        {label: "W/m²", conversion: function(x) { return x; }, precision: 0}
                    ],
                    scale: {
                        bounds: [0, 80000],
                        gradient: µ.segmentedColorScale([
                            [0, [15, 4, 96]],
                            [250, [30, 8, 180]],
                            [1000, [121, 102, 2]],
                            [2000, [118, 161, 66]],
                            [4000, [50, 102, 219]],
                            [8000, [19, 131, 193]],
                            [16000, [59, 204, 227]],
                            [64000, [241, 1, 45]],
                            [80000, [243, 0, 241]]
                        ])
                    }
                });
            }
        },

        "total_cloud_water": {
            matches: _.matches({param: "wind", overlayType: "total_cloud_water"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "total_cloud_water",
                    description: localize({
                        name: {en: "Total Cloud Water", ja: "雲水量"},
                        qualifier: ""
                    }),
                    paths: [gfs1p0degPath(attr, "total_cloud_water", attr.surface, attr.level)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "kg/m²", conversion: function(x) { return x; }, precision: 3}
                    ],
                    scale: {
                        bounds: [0, 1],
                        gradient: µ.segmentedColorScale([
                            [0.0, [5, 5, 89]],
                            [0.2, [170, 170, 230]],
                            [1.0, [255, 255, 255]]
                        ])
                    }
                });
            }
        },

        "total_precipitable_water": {
            matches: _.matches({param: "wind", overlayType: "total_precipitable_water"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "total_precipitable_water",
                    description: localize({
                        name: {en: "Total Precipitable Water", ja: "可降水量"},
                        qualifier: ""
                    }),
                    paths: [gfs1p0degPath(attr, "total_precipitable_water")],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "kg/m²", conversion: function(x) { return x; }, precision: 3}
                    ],
                    scale: {
                        bounds: [0, 70],
                        gradient:
                            µ.segmentedColorScale([
                                [0, [230, 165, 30]],
                                [10, [120, 100, 95]],
                                [20, [40, 44, 92]],
                                [30, [21, 13, 193]],
                                [40, [75, 63, 235]],
                                [60, [25, 255, 255]],
                                [70, [150, 255, 255]]
                            ])
                    }
                });
            }
        },

        "mean_sea_level_pressure": {
            matches: _.matches({param: "wind", overlayType: "mean_sea_level_pressure"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "mean_sea_level_pressure",
                    description: localize({
                        name: {en: "Mean Sea Level Pressure", ja: "海面更正気圧"},
                        qualifier: ""
                    }),
                    paths: [gfs1p0degPath(attr, "mean_sea_level_pressure")],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "hPa", conversion: function(x) { return x / 100; }, precision: 0},
                        {label: "mmHg", conversion: function(x) { return x / 133.322387415; }, precision: 0},
                        {label: "inHg", conversion: function(x) { return x / 3386.389; }, precision: 1}
                    ],
                    scale: {
                        bounds: [92000, 105000],
                        gradient: µ.segmentedColorScale([
                            [92000, [40, 0, 0]],
                            [95000, [187, 60, 31]],
                            [96500, [137, 32, 30]],
                            [98000, [16, 1, 43]],
                            [100500, [36, 1, 93]],
                            [101300, [241, 254, 18]],
                            [103000, [228, 246, 223]],
                            [105000, [255, 255, 255]]
                        ])
                    }
                });
            }
        },


         */


        "prog_currents": {
            matches: _.matches({param: "ocean"}),
            create: function(attr) {
                    return buildProduct({
                        date: gfsDate(attr),
                        field: "vector",
                        type: "prog_currents",
                        description: localize({
                            name: {en: "Ocean Currents"},
                            qualifier: {en: " @ Surface"}
                        }),
                        paths: [ earthDataServiceUrl(attr, 'prog_currents') ],

                        builder: function(file) {
                            var uData = file[0].data, vData = file[1].data;
                            return {
                                header: file[0].header,
                                interpolate: bilinearInterpolateVector,
                                data: function(i) {
                                    var u = uData[i], v = vData[i];
                                    return µ.isValue(u) && µ.isValue(v) ? [u, v] : null;
                                }
                            }
                        },
                        units: [
                            {label: "m/s",  conversion: function(x) { return x; },            precision: 2},
                            {label: "km/h", conversion: function(x) { return x * 3.6; },      precision: 1},
                            {label: "kn",   conversion: function(x) { return x * 1.943844; }, precision: 1},
                            {label: "mph",  conversion: function(x) { return x * 2.236936; }, precision: 1}
                        ],
                        scale: {
                            bounds: [0, 2.5],
                            gradient: µ.segmentedColorScale([
                                [0.0,  [18, 32, 86]],     // 0.0: 정체/극미약 (딥 네이비-블루)
                                [0.2,  [24, 95, 225]],    // 0.2: 약한 흐름 (로열 블루)
                                [0.5,  [22, 195, 220]],   // 0.5: 완만한 흐름 (시안/청록)
                                [0.8,  [45, 225, 130]],   // 0.8: 보통 유속 (에메랄드 그린)
                                [1.2,  [245, 220, 45]],   // 1.2: 다소 빠른 흐름 (옐로우)
                                [1.7,  [245, 125, 30]],   // 1.7: 강한 해류 (오렌지)
                                [2.2,  [230, 35, 55]],    // 2.2: 매우 강한 해류 (레드)
                                [2.5,  [165, 20, 95]]     // 2.5: 최대 상한 (크림슨/마젠타)
                            ])
                        },
                        particles: {velocityScale: 1/4400, maxIntensity: 0.7}
                    });
            }
        },

        "prog_temperature": {
            matches: _.matches({param: "ocean", overlayType: "prog_temperature"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "prog_temperature",
                    description: localize({
                        name: {en: "Temp"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [ earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "°C", conversion: function(x) { return x ; },       precision: 1}
                    ],
                    scale: {
                        bounds: [-2.5, 35],
                        gradient: µ.segmentedColorScale([
                            [-2.5,  [38, 12, 84]],     // -2.5°C: 극저수온/결빙 임계 (딥 퍼플-인디고)
                            [-1.8,  [72, 34, 168]],    // -1.8°C: 해수 빙점 부근 (바이올렛 블루)
                            [0.0,   [45, 130, 220]],   //  0.0°C: 어는점 기준선 (선명한 블루)
                            [5.0,   [25, 195, 215]],   //  5.0°C: 한랭 해역 (시안/청록)
                            [10.0,  [42, 210, 135]],   // 10.0°C: 냉온대 해역 (에메랄드/그린)
                            [15.0,  [145, 225, 65]],   // 15.0°C: 온대 중간수온 (옐로우 그린)
                            [20.0,  [250, 220, 45]],   // 20.0°C: 난수 진입 (옐로우)
                            [25.0,  [245, 135, 30]],   // 25.0°C: 난류/아열대 (오렌지)
                            [30.0,  [225, 45, 45]],    // 30.0°C: 고수온 (레드)
                            [35.0,  [135, 20, 75]]     // 35.0°C: 최고 수온 상한 (다크 크림슨/마젠타)
                        ])
                    }
                });
            }
        },
        "prog_salinity": {
            matches: _.matches({param: "ocean", overlayType: "prog_salinity"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "prog_salinity",
                    description: localize({
                        name: {en: "Salinity"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [ earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "psu", conversion: function(x) { return x ; },    precision: 1},
                    ],
                    scale: {
                        bounds: [0, 45],
                        gradient: µ.segmentedColorScale([
                            [0,  [139, 90, 43]],    // 0: 담수/하구 토사 톤 (갈색)
                            [15, [110, 125, 65]],   // 15: 저염분 연안 (올리브)
                            [25, [32, 110, 150]],   // 25: 기수/연안 혼합수 (청록)
                            [30, [24, 78, 196]],    // 30: 연안~외양 경계 (딥 블루)
                            [33, [18, 165, 224]],   // 33: 외양 진입 (스카이/시안 블루)
                            [35, [35, 235, 195]],   // 35: 표준 해수 부근 (청록-민트 포인트)
                            [37, [245, 195, 45]],   // 37: 고염분 (앰버/옐로우)
                            [45, [215, 45, 95]]     // 45: 최고 염분 (크림슨/마젠타)
                        ])
                    }
                });
            }
        },

        "ice_currents": {
            matches: _.matches({param: "ice"}),
            create: function(attr) {
                return buildProduct({
                    date: gfsDate(attr),
                    field: "vector",
                    type: "ice_currents",
                    description: localize({
                        name: {en: "Ice Currents"},
                        qualifier: {en: " @ Surface"}
                    }),
                    paths: [ earthDataServiceUrl(attr, 'ice_currents') ],

                    builder: function(file) {
                        var uData = file[0].data, vData = file[1].data;
                        return {
                            header: file[0].header,
                            interpolate: bilinearInterpolateVector,
                            data: function(i) {
                                var u = uData[i], v = vData[i];
                                return µ.isValue(u) && µ.isValue(v) ? [u, v] : null;
                            }
                        }
                    },
                    units: [
                        {label: "m/s",  conversion: function(x) { return x; },            precision: 2},
                        {label: "km/h", conversion: function(x) { return x * 3.6; },      precision: 1},
                        {label: "kn",   conversion: function(x) { return x * 1.943844; }, precision: 1},
                        {label: "mph",  conversion: function(x) { return x * 2.236936; }, precision: 1}
                    ],
                    scale: {
                        bounds: [0, 1.5],
                        gradient: µ.segmentedColorScale([
                            [0.0,  [22, 36, 92]],     // 0.0 m/s: 정체/극미약 (딥 인디고-블루)
                            [0.15, [25, 110, 225]],   // 0.15 m/s: 미약한 표류 (로열 블루)
                            [0.35, [24, 195, 215]],   // 0.35 m/s: 약한~보통 표류 (시안)
                            [0.6,  [55, 220, 125]],   // 0.6 m/s: 보통 이동 (에메랄드 그린)
                            [0.9,  [245, 215, 45]],   // 0.9 m/s: 다소 빠른 표류 (옐로우)
                            [1.2,  [245, 120, 25]],   // 1.2 m/s: 빠른 유빙 이동 (오렌지)
                            [1.5,  [220, 35, 65]]     // 1.5 m/s: 최대 상한 (크림슨/레드)
                        ])
                    },
                    particles: {velocityScale: 1/4400, maxIntensity: 0.7}
                });
            }
        },
        "ice_temperature": {
            matches: _.matches({param: "ice", overlayType: "ice_temperature"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "ice_temperature",
                    description: localize({
                        name: {en: "Temp"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [ earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "°C", conversion: function(x) { return x ; },       precision: 1},
                        {label: "°F", conversion: function(x) { return  (x * 1.8) + 32; }, precision: 1},
                        {label: "K",  conversion: function(x) { return x + 273.15; },  precision: 1}
                    ],
                    scale: {
                        bounds: [-40, 0],
                        gradient: µ.segmentedColorScale([
                            [-40, [45, 15, 95]],     // -40°C: 극저온 (딥 퍼플-인디고)
                            [-30, [50, 45, 175]],    // -30°C: 매우 낮은 해빙 온도 (바이올렛 블루)
                            [-22, [30, 125, 225]],   // -22°C: 강추위 해빙 (로열/스카이 블루)
                            [-15, [25, 195, 215]],   // -15°C: 중저온 해빙 (시안/청록)
                            [-10, [65, 215, 130]],   // -10°C: 중간 온도 (에메랄드 그린)
                            [-5,  [245, 215, 45]],   //  -5°C: 융해 전이 (옐로우)
                            [-2,  [245, 125, 30]],   //  -2°C: 해수 빙점 부근 (오렌지)
                            [0,   [215, 35, 65]]     //   0°C: 어는점/융해 상한 (크림슨/레드)
                        ])
                    }
                });
            }
        },
        "ice_coverage": {
            matches: _.matches({param: "ice", overlayType: "ice_coverage"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "ice_coverage",
                    description: localize({
                        name: {en: ""},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [ earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "", conversion: function(x) { return x ; },       precision: 1}
                    ],
                    scale: {
                        bounds: [0, 1],
                        gradient: µ.segmentedColorScale([
                            [0.0,  [20, 42, 95]],     // 0.0: 해빙 없음 / 개방 수역 (딥 블루)
                            [0.15, [28, 115, 215]],   // 0.15: 저밀도 유빙 진입 (로열/스카이 블루)
                            [0.35, [28, 195, 215]],   // 0.35: 부분 해빙 (시안)
                            [0.55, [75, 215, 130]],   // 0.55: 중간 밀집도 (에메랄드 그린)
                            [0.75, [245, 215, 50]],   // 0.75: 고밀도 해빙 진입 (옐로우)
                            [0.9,  [245, 125, 30]],   // 0.9: 매우 높은 피복률 (오렌지)
                            [1.0,  [225, 45, 70]]     // 1.0: 완전 피복 / 최대 상한 (크림슨/레드)
                        ])
                    }
                });
            }
        },
        "ice_thickness": {
            matches: _.matches({param: "ice", overlayType: "ice_thickness"}),
            create: function(attr) {
                return buildProduct({
                    field: "scalar",
                    type: "ice_thickness",
                    description: localize({
                        name: {en: "m"},
                        qualifier: {en: " @ " + describeSurface(attr)}
                    }),
                    paths: [ earthDataServiceUrl(attr, attr.overlayType)],
                    date: gfsDate(attr),
                    builder: function(file) {
                        var record = file[0], data = record.data;
                        return {
                            header: record.header,
                            interpolate: bilinearInterpolateScalar,
                            data: function(i) {
                                return data[i];
                            }
                        }
                    },
                    units: [
                        {label: "m", conversion: function(x) { return x ; },       precision: 1}
                    ],
                    scale: {
                        bounds: [0, 2],
                        gradient: µ.segmentedColorScale([
                            /*
                             [0.0, [20, 42, 95]],     // 0.0 m: 결빙 없음 / 극박막 (딥 블루)
                            [0.2, [28, 115, 215]],   // 0.2 m: 얇은 얼음 (로열/스카이 블루)
                            [0.5, [28, 195, 215]],   // 0.5 m: 중간-얇은 해빙 (시안)
                            [0.8, [75, 215, 130]],   // 0.8 m: 중간 두께 (에메랄드 그린)
                            [1.2, [245, 215, 50]],   // 1.2 m: 다소 두꺼운 해빙 (옐로우)
                            [1.6, [245, 125, 30]],   // 1.6 m: 두꺼운 해빙 (오렌지)
                            [2.0, [225, 45, 70]]     // 2.0 m: 최대 상한 / 두꺼운 결빙 (크림슨/레드)

                             */
                            [0.0, [18, 36, 88]],
                            [0.15, [25, 115, 225]],
                            [0.35, [30, 195, 210]],
                            [0.6, [90, 215, 115]],
                            [1.0, [245, 215, 45]],
                            [1.5, [245, 115, 30]],
                            [2.0, [200, 30, 65]]
                        ])

                    }
                });
            }
        },

        "off": {
            matches: _.matches({overlayType: "off"}),
            create: function() {
                return null;
            }
        }
    };



    function dataSource(header) {
        // noinspection FallthroughInSwitchStatementJS
        switch (header.center || header.centerName) {
            case -3:
                return "RTOFS Global Model Forecast Information";
            case 7:
            case "US National Weather Service, National Centres for Environmental Prediction (NCEP)":
                return "GFS / NCEP / US National Weather Service";
            default:
                return header.centerName;
        }
    }

    function bilinearInterpolateScalar(x, y, g00, g10, g01, g11) {
        var rx = (1 - x);
        var ry = (1 - y);
        return g00 * rx * ry + g10 * x * ry + g01 * rx * y + g11 * x * y;
    }

    function bilinearInterpolateVector(x, y, g00, g10, g01, g11) {
        var rx = (1 - x);
        var ry = (1 - y);
        var a = rx * ry,  b = x * ry,  c = rx * y,  d = x * y;
        var u = g00[0] * a + g10[0] * b + g01[0] * c + g11[0] * d;
        var v = g00[1] * a + g10[1] * b + g01[1] * c + g11[1] * d;
        return [u, v, Math.sqrt(u * u + v * v)];
    }

    /**
     * Builds an interpolator for the specified data in the form of JSON-ified GRIB files. Example:
     *
     *     [
     *       {
     *         "header": {
     *           "refTime": "2013-11-30T18:00:00.000Z",
     *           "parameterCategory": 2,
     *           "parameterNumber": 2,
     *           "surface1Type": 100,
     *           "surface1Value": 100000.0,
     *           "forecastTime": 6,
     *           "scanMode": 0,
     *           "nx": 360,
     *           "ny": 181,
     *           "lo1": 0,
     *           "la1": 90,
     *           "lo2": 359,
     *           "la2": -90,
     *           "dx": 1,
     *           "dy": 1
     *         },
     *         "data": [3.42, 3.31, 3.19, 3.08, 2.96, 2.84, 2.72, 2.6, 2.47, ...]
     *       }
     *     ]
     *
     */
    function buildGrid(builder) {
        // var builder = createBuilder(data);

        var header = builder.header;
        var λ0 = header.lo1, φ0 = header.la1;  // the grid's origin (e.g., 0.0E, 90.0N)
        var Δλ = header.dx, Δφ = header.dy;    // distance between grid points (e.g., 2.5 deg lon, 2.5 deg lat)
        var ni = header.nx, nj = header.ny;    // number of grid points W-E and N-S (e.g., 144 x 73)

       /*
        var date = new Date(header.refTime);
        date.setHours(date.getHours() + header.forecastTime);
        */

        var refTime = new Date(header.refTime);
        var forecastTime = +(header.forecastTime || 0);
        var date = new Date(refTime.getTime());
        date.setHours(date.getHours() + forecastTime);


        // Scan mode 0 assumed. Longitude increases from λ0, and latitude decreases from φ0.
        // http://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_table3-4.shtml
        var grid = [], p = 0;
        var isContinuous = Math.floor(ni * Δλ) >= 360;
        for (var j = 0; j < nj; j++) {
            var row = [];
            for (var i = 0; i < ni; i++, p++) {
                row[i] = builder.data(p);
            }
            if (isContinuous) {
                // For wrapped grids, duplicate first column as last column to simplify interpolation logic
                row.push(row[0]);
            }
            grid[j] = row;
        }

        function interpolate(λ, φ) {
            var i = µ.floorMod(λ - λ0, 360) / Δλ;  // calculate longitude index in wrapped range [0, 360)
            var j = (φ0 - φ) / Δφ;                 // calculate latitude index in direction +90 to -90

            //         1      2           After converting λ and φ to fractional grid indexes i and j, we find the
            //        fi  i   ci          four points "G" that enclose point (i, j). These points are at the four
            //         | =1.4 |           corners specified by the floor and ceiling of i and j. For example, given
            //      ---G--|---G--- fj 8   i = 1.4 and j = 8.3, the four surrounding grid points are (1, 8), (2, 8),
            //    j ___|_ .   |           (1, 9) and (2, 9).
            //  =8.3   |      |
            //      ---G------G--- cj 9   Note that for wrapped grids, the first column is duplicated as the last
            //         |      |           column, so the index ci can be used without taking a modulo.

            var fi = Math.floor(i), ci = fi + 1;
            var fj = Math.floor(j), cj = fj + 1;

            var row;
            if ((row = grid[fj])) {
                var g00 = row[fi];
                var g10 = row[ci];
                if (µ.isValue(g00) && µ.isValue(g10) && (row = grid[cj])) {
                    var g01 = row[fi];
                    var g11 = row[ci];
                    if (µ.isValue(g01) && µ.isValue(g11)) {
                        // All four points found, so interpolate the value.
                        return builder.interpolate(i - fi, j - fj, g00, g10, g01, g11);
                    }
                }
            }
            // console.log("cannot interpolate: " + λ + "," + φ + ": " + fi + " " + ci + " " + fj + " " + cj);
            return null;
        }

        return {
            source: dataSource(header),
            refTime: refTime,
            forecastTime: forecastTime,
            date: date,
            interpolate: interpolate,
            forEachPoint: function(cb) {
                for (var j = 0; j < nj; j++) {
                    var row = grid[j] || [];
                    for (var i = 0; i < ni; i++) {
                        cb(µ.floorMod(180 + λ0 + i * Δλ, 360) - 180, φ0 - j * Δφ, row[i]);
                    }
                }
            }
        };
    }

    function productsFor(attributes) {
        var attr = _.clone(attributes), results = [];
        _.values(FACTORIES).forEach(function(factory) {
            if (factory.matches(attr)) {
                results.push(factory.create(attr));
            }
        });
        return results.filter(µ.isValue);
    }

    return {
        overlayTypes: d3.set(_.keys(FACTORIES)),
        productsFor: productsFor
    };

}();
