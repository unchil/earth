(function() {
    "use strict";

    var SECOND = 1000;
    var MINUTE = 60 * SECOND;
    var HOUR = 60 * MINUTE;
    var MAX_TASK_TIME = 100;                  // amount of time before a task yields control (millis)
    var MIN_SLEEP_TIME = 25;                  // amount of time a task waits before resuming (millis)
    var MIN_MOVE = 4;                         // slack before a drag operation beings (pixels)
    var MOVE_END_WAIT = 1000;                 // time to wait for a move operation to be considered done (millis)

    var OVERLAY_ALPHA = Math.floor(0.4*255);  // overlay transparency (on scale [0, 255])
    var INTENSITY_SCALE_STEP = 10;            // step size of particle intensity color scale
    var MAX_PARTICLE_AGE = 100;               // max number of frames a particle is drawn before regeneration
    var PARTICLE_LINE_WIDTH = 1.0;            // line width of a drawn particle
    var PARTICLE_MULTIPLIER = 7;              // particle count scalar (completely arbitrary--this values looks nice)
    var PARTICLE_REDUCTION = 0.75;            // reduce particle count to this much of normal for mobile devices
    var FRAME_RATE = 40;                      // desired milliseconds per frame

    var NULL_WIND_VECTOR = [NaN, NaN, null];  // singleton for undefined location outside the vector field [u, v, mag]
    var HOLE_VECTOR = [NaN, NaN, null];       // singleton that signifies a hole in the vector field
    var TRANSPARENT_BLACK = [0, 0, 0, 0];     // singleton 0 rgba
    var REMAINING = "▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫";   // glyphs for remaining progress bar
    var COMPLETED = "▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪";   // glyphs for completed progress bar

    var view = µ.view();
    var log = µ.log();


    var report = function() {
        var s = d3.select("#status"), p = d3.select("#progress"), total = REMAINING.length;
        return {
            status: function(msg) {
                return s.classed("bad") ? s : s.text(msg);  // errors are sticky until reset
            },
            error: function(err) {
                var msg = err.status ? err.status + " " + err.message : err;
                switch (err.status) {
                    case -1: msg = "Server Down"; break;
                    case 404: msg = "No Data"; break;
                }
                log.error(err);
                return s.classed("bad", true).text(msg);
            },
            reset: function() {
                return s.classed("bad", false).text("");
            },
            progress: function(amount) {  // amount of progress to report in the range [0, 1]
                if (0 <= amount && amount < 1) {
                    var i = Math.ceil(amount * total);
                    var bar = COMPLETED.substr(0, i) + REMAINING.substr(0, total - i);
                    return p.classed("invisible", false).text(bar);
                }
                return p.classed("invisible", true).text("");  // progress complete
            }
        };
    }();

    function newAgent() {
        return µ.newAgent().on({"reject": report.error, "fail": report.error});
    }

    var configuration =
        µ.buildConfiguration(globes, products.overlayTypes);  // holds the page's current configuration settings
    var inputController = buildInputController();             // interprets drag/zoom operations
    var meshAgent = newAgent();      // map data for the earth
    var globeAgent = newAgent();     // the model of the globe
    var gridAgent = newAgent();      // the grid of weather data
    var rendererAgent = newAgent();  // the globe SVG renderer
    var fieldAgent = newAgent();     // the interpolated wind vector field
    var animatorAgent = newAgent();  // the wind animator
    var overlayAgent = newAgent();   // color overlay over the animation


    function buildInputController() {
        var globe, op = null;

        /**
         * @returns {Object} an object to represent the state for one move operation.
         */
        function newOp(startMouse, startScale) {
            return {
                type: "click",  // initially assumed to be a click operation
                startMouse: startMouse,
                startScale: startScale,
                manipulator: globe.manipulator(startMouse, startScale)
            };
        }

        var zoom = d3.behavior.zoom()
            .on("zoomstart", function() {
                op = op || newOp(d3.mouse(this), zoom.scale());  // a new operation begins
            })
            .on("zoom", function() {
                var currentMouse = d3.mouse(this), currentScale = d3.event.scale;
                op = op || newOp(currentMouse, 1);  // Fix bug on some browsers where zoomstart fires out of order.
                if (op.type === "click" || op.type === "spurious") {
                    var distanceMoved = µ.distance(currentMouse, op.startMouse);
                    if (currentScale === op.startScale && distanceMoved < MIN_MOVE) {
                        // to reduce annoyance, ignore op if mouse has barely moved and no zoom is occurring
                        op.type = distanceMoved > 0 ? "click" : "spurious";
                        return;
                    }
                    dispatch.trigger("moveStart");
                    op.type = "drag";
                }
                if (currentScale != op.startScale) {
                    op.type = "zoom";  // whenever a scale change is detected, (stickily) switch to a zoom operation
                }

                // when zooming, ignore whatever the mouse is doing--really cleans up behavior on touch devices
                op.manipulator.move(op.type === "zoom" ? null : currentMouse, currentScale);
                dispatch.trigger("move");
            })
            .on("zoomend", function() {
                op.manipulator.end();
                if (op.type === "click") {
                    dispatch.trigger("click", op.startMouse, globe.projection.invert(op.startMouse) || []);
                }
                else if (op.type !== "spurious") {
                    signalEnd();
                }
                op = null;  // the drag/zoom/click operation is over
            });

        var signalEnd = _.debounce(function() {
            if (!op || op.type !== "drag" && op.type !== "zoom") {
                configuration.save({orientation: globe.orientation()}, {source: "moveEnd"});
                dispatch.trigger("moveEnd");
            }
        }, MOVE_END_WAIT);  // wait for a bit to decide if user has stopped moving the globe

        d3.select("#display").call(zoom);
        d3.select("#show-location").on("click", function() {
            if (navigator.geolocation) {
                report.status("Finding current position...");
                navigator.geolocation.getCurrentPosition(function(pos) {
                    report.status("");
                    var coord = [pos.coords.longitude, pos.coords.latitude], rotate = globe.locate(coord);
                    if (rotate) {
                        globe.projection.rotate(rotate);
                        configuration.save({orientation: globe.orientation()});  // triggers reorientation
                    }
                    dispatch.trigger("click", globe.projection(coord), coord);
                }, log.error);
            }
        });

        function reorient() {
            var options = arguments[3] || {};
            if (!globe || options.source === "moveEnd") {
                // reorientation occurred because the user just finished a move operation, so globe is already
                // oriented correctly.
                return;
            }
            dispatch.trigger("moveStart");
            globe.orientation(configuration.get("orientation"), view);
            zoom.scale(globe.projection.scale());
            dispatch.trigger("moveEnd");
        }

        var dispatch = _.extend({
            globe: function(_) {
                if (_) {
                    globe = _;
                    zoom.scaleExtent(globe.scaleExtent());
                    reorient();
                }
                return _ ? this : globe;
            }
        }, Backbone.Events);
        return dispatch.listenTo(configuration, "change:orientation", reorient);
    }

    function buildMesh(resource) {
        var cancel = this.cancel;
        report.status("Downloading...");
        return µ.loadJson(resource).then(function(topo) {
            if (cancel.requested) return null;
            log.time("building meshes");
            var o = topo.objects;
            var coastLo = topojson.feature(topo, µ.isMobile() ? o.coastline_tiny : o.coastline_110m);
            var coastHi = topojson.feature(topo, µ.isMobile() ? o.coastline_110m : o.coastline_50m);
            var lakesLo = topojson.feature(topo, µ.isMobile() ? o.lakes_tiny : o.lakes_110m);
            var lakesHi = topojson.feature(topo, µ.isMobile() ? o.lakes_110m : o.lakes_50m);
            log.timeEnd("building meshes");
            return {
                coastLo: coastLo,
                coastHi: coastHi,
                lakesLo: lakesLo,
                lakesHi: lakesHi
            };
        });
    }

    function buildGlobe(projectionName) {
        var builder = globes.get(projectionName);
        if (!builder) {
            return when.reject("Unknown projection: " + projectionName);
        }
        return when(builder(view));
    }

    // Some hacky stuff to ensure only one download can be in progress at a time.
    var downloadsInProgress = 0;

    function buildGrids() {
        report.status("Downloading...");
        log.time("build grids");
        // UNDONE: upon failure to load a product, the unloaded product should still be stored in the agent.
        //         this allows us to use the product for navigation and other state.
        var cancel = this.cancel;
        downloadsInProgress++;
        var loaded = when.map(products.productsFor(configuration.attributes), function(product) {
            return product.load(cancel);
        });
        return when.all(loaded).then(function(products) {
            log.time("build grids");
            return {primaryGrid: products[0], overlayGrid: products[1] || products[0]};
        }).ensure(function() {
            downloadsInProgress--;
        });
    }

    function navigate(step) {
        if (downloadsInProgress > 0) {
            log.debug("Download in progress--ignoring nav request.");
            return;
        }
        var next = gridAgent.value().primaryGrid.navigate(step);
        if (next) {
            var param = configuration.attributes.param
            configuration.save(µ.dateToConfig(next, param));
        }
    }

    function buildRenderer(mesh, globe) {
        if (!mesh || !globe) return null;

        report.status("Rendering Globe...");
        log.time("rendering map");

        // UNDONE: better way to do the following?
        var dispatch = _.clone(Backbone.Events);
        if (rendererAgent._previous) {
            rendererAgent._previous.stopListening();
        }
        rendererAgent._previous = dispatch;

        // First clear map and foreground svg contents.
        µ.removeChildren(d3.select("#map").node());
        µ.removeChildren(d3.select("#foreground").node());
        // Create new map svg elements.
        globe.defineMap(d3.select("#map"), d3.select("#foreground"));

        var path = d3.geo.path().projection(globe.projection).pointRadius(7);
        var coastline = d3.select(".coastline");
        var lakes = d3.select(".lakes");
        d3.selectAll("path").attr("d", path);  // do an initial draw -- fixes issue with safari

        function drawLocationMark(point, coord) {
            // show the location on the map if defined
            if (fieldAgent.value() && !fieldAgent.value().isInsideBoundary(point[0], point[1])) {
                // UNDONE: Sometimes this is invoked on an old, released field, because new one has not been
                //         built yet, causing the mark to not get drawn.
                return;  // outside the field boundary, so ignore.
            }
            if (coord && _.isFinite(coord[0]) && _.isFinite(coord[1])) {
                var mark = d3.select(".location-mark");
                if (!mark.node()) {
                    mark = d3.select("#foreground").append("path").attr("class", "location-mark");
                }
                mark.datum({type: "Point", coordinates: coord}).attr("d", path);
            }
        }

        // Draw the location mark if one is currently visible.
        if (activeLocation.point && activeLocation.coord) {
            drawLocationMark(activeLocation.point, activeLocation.coord);
        }

        // Throttled draw method helps with slow devices that would get overwhelmed by too many redraw events.
        var REDRAW_WAIT = 5;  // milliseconds
        var doDraw_throttled = _.throttle(doDraw, REDRAW_WAIT, {leading: false});

        function doDraw() {
            d3.selectAll("path").attr("d", path);
            rendererAgent.trigger("redraw");
            doDraw_throttled = _.throttle(doDraw, REDRAW_WAIT, {leading: false});
        }

        // Attach to map rendering events on input controller.
        dispatch.listenTo(
            inputController, {
                moveStart: function() {
                    coastline.datum(mesh.coastLo);
                    lakes.datum(mesh.lakesLo);
                    rendererAgent.trigger("start");
                },
                move: function() {
                    doDraw_throttled();
                },
                moveEnd: function() {
                    coastline.datum(mesh.coastHi);
                    lakes.datum(mesh.lakesHi);
                    d3.selectAll("path").attr("d", path);
                    rendererAgent.trigger("render");
                },
                click: drawLocationMark
            });

        // Finally, inject the globe model into the input controller. Do it on the next event turn to ensure
        // renderer is fully set up before events start flowing.
        when(true).then(function() {
            inputController.globe(globe);
        });

        log.timeEnd("rendering map");
        return "ready";
    }

    function createMask(globe) {
        if (!globe) return null;

        log.time("render mask");

        // Create a detached canvas, ask the model to define the mask polygon, then fill with an opaque color.
        var width = view.width, height = view.height;
        var canvas = d3.select(document.createElement("canvas")).attr("width", width).attr("height", height).node();
        var context = globe.defineMask(canvas.getContext("2d"));
        context.fillStyle = "rgba(255, 0, 0, 1)";
        context.fill();
        // d3.select("#display").node().appendChild(canvas);  // make mask visible for debugging

        var imageData = context.getImageData(0, 0, width, height);
        var data = imageData.data;  // layout: [r, g, b, a, r, g, b, a, ...]
        log.timeEnd("render mask");
        return {
            imageData: imageData,
            isVisible: function(x, y) {
                var i = (y * width + x) * 4;
                return data[i + 3] > 0;  // non-zero alpha means pixel is visible
            },
            set: function(x, y, rgba) {
                var i = (y * width + x) * 4;
                data[i    ] = rgba[0];
                data[i + 1] = rgba[1];
                data[i + 2] = rgba[2];
                data[i + 3] = rgba[3];
                return this;
            }
        };
    }

    function createField(columns, bounds, mask) {

        /**
         * @returns {Array} wind vector [u, v, magnitude] at the point (x, y), or [NaN, NaN, null] if wind
         *          is undefined at that point.
         */
        function field(x, y) {
            var column = columns[Math.round(x)];
            return column && column[Math.round(y)] || NULL_WIND_VECTOR;
        }

        /**
         * @returns {boolean} true if the field is valid at the point (x, y)
         */
        field.isDefined = function(x, y) {
            return field(x, y)[2] !== null;
        };

        /**
         * @returns {boolean} true if the point (x, y) lies inside the outer boundary of the vector field, even if
         *          the vector field has a hole (is undefined) at that point, such as at an island in a field of
         *          ocean currents.
         */
        field.isInsideBoundary = function(x, y) {
            return field(x, y) !== NULL_WIND_VECTOR;
        };

        // Frees the massive "columns" array for GC. Without this, the array is leaked (in Chrome) each time a new
        // field is interpolated because the field closure's context is leaked, for reasons that defy explanation.
        field.release = function() {
            columns = [];
        };

        field.randomize = function(o) {  // UNDONE: this method is terrible
            var x, y;
            var safetyNet = 0;
            do {
                x = Math.round(_.random(bounds.x, bounds.xMax));
                y = Math.round(_.random(bounds.y, bounds.yMax));
            } while (!field.isDefined(x, y) && safetyNet++ < 30);
            o.x = x;
            o.y = y;
            return o;
        };

        field.overlay = mask.imageData;

        return field;
    }

    function distort(projection, λ, φ, x, y, scale, wind) {
        var u = wind[0] * scale;
        var v = wind[1] * scale;
        var d = µ.distortion(projection, λ, φ, x, y);

        // Scale distortion vectors by u and v, then add.
        wind[0] = d[0] * u + d[2] * v;
        wind[1] = d[1] * u + d[3] * v;
        return wind;
    }

    function interpolateField(globe, grids) {
        if (!globe || !grids) return null;

        var mask = createMask(globe);
        var primaryGrid = grids.primaryGrid;
        var overlayGrid = grids.overlayGrid;

        log.time("interpolating field");
        var d = when.defer(), cancel = this.cancel;

        var projection = globe.projection;
        var bounds = globe.bounds(view);
        // How fast particles move on the screen (arbitrary value chosen for aesthetics).
        var velocityScale = bounds.height * primaryGrid.particles.velocityScale;

        var columns = [];
        var point = [];
        var x = bounds.x;
        var interpolate = primaryGrid.interpolate;
        var overlayInterpolate = overlayGrid.interpolate;
        var hasDistinctOverlay = primaryGrid !== overlayGrid;
        var scale = overlayGrid.scale;

        function interpolateColumn(x) {
            var column = [];
            for (var y = bounds.y; y <= bounds.yMax; y += 2) {
                if (mask.isVisible(x, y)) {
                    point[0] = x; point[1] = y;
                    var coord = projection.invert(point);
                    var color = TRANSPARENT_BLACK;
                    var wind = null;
                    if (coord) {
                        var λ = coord[0], φ = coord[1];
                        if (isFinite(λ)) {
                            wind = interpolate(λ, φ);
                            var scalar = null;
                            if (wind) {
                                wind = distort(projection, λ, φ, x, y, velocityScale, wind);
                                scalar = wind[2];
                            }
                            if (hasDistinctOverlay) {
                                scalar = overlayInterpolate(λ, φ);
                            }
                            if (µ.isValue(scalar)) {
                                color = scale.gradient(scalar, OVERLAY_ALPHA);
                            }
                        }
                    }
                    column[y+1] = column[y] = wind || HOLE_VECTOR;
                    mask.set(x, y, color).set(x+1, y, color).set(x, y+1, color).set(x+1, y+1, color);
                }
            }
            columns[x+1] = columns[x] = column;
        }

        report.status("");

        (function batchInterpolate() {
            try {
                if (!cancel.requested) {
                    var start = Date.now();
                    while (x < bounds.xMax) {
                        interpolateColumn(x);
                        x += 2;
                        if ((Date.now() - start) > MAX_TASK_TIME) {
                            // Interpolation is taking too long. Schedule the next batch for later and yield.
                            report.progress((x - bounds.x) / (bounds.xMax - bounds.x));
                            setTimeout(batchInterpolate, MIN_SLEEP_TIME);
                            return;
                        }
                    }
                }
                d.resolve(createField(columns, bounds, mask));
            }
            catch (e) {
                d.reject(e);
            }
            report.progress(1);  // 100% complete
            log.timeEnd("interpolating field");
        })();

        return d.promise;
    }

    function animate(globe, field, grids) {
        if (!globe || !field || !grids) return;

        var cancel = this.cancel;
        var bounds = globe.bounds(view);
        // maxIntensity is the velocity at which particle color intensity is maximum
        var colorStyles = µ.windIntensityColorScale(INTENSITY_SCALE_STEP, grids.primaryGrid.particles.maxIntensity);
        var buckets = colorStyles.map(function() { return []; });
        var particleCount = Math.round(bounds.width * PARTICLE_MULTIPLIER);
        if (µ.isMobile()) {
            particleCount *= PARTICLE_REDUCTION;
        }
        var fadeFillStyle = µ.isFF() ? "rgba(0, 0, 0, 0.95)" : "rgba(0, 0, 0, 0.97)";  // FF Mac alpha behaves oddly

        log.debug("particle count: " + particleCount);
        var particles = [];
        for (var i = 0; i < particleCount; i++) {
            particles.push(field.randomize({age: _.random(0, MAX_PARTICLE_AGE)}));
        }

        function evolve() {
            buckets.forEach(function(bucket) { bucket.length = 0; });
            particles.forEach(function(particle) {
                if (particle.age > MAX_PARTICLE_AGE) {
                    field.randomize(particle).age = 0;
                }
                var x = particle.x;
                var y = particle.y;
                var v = field(x, y);  // vector at current position
                var m = v[2];
                if (m === null) {
                    particle.age = MAX_PARTICLE_AGE;  // particle has escaped the grid, never to return...
                }
                else {
                    var xt = x + v[0];
                    var yt = y + v[1];
                    if (field.isDefined(xt, yt)) {
                        // Path from (x,y) to (xt,yt) is visible, so add this particle to the appropriate draw bucket.
                        particle.xt = xt;
                        particle.yt = yt;
                        buckets[colorStyles.indexFor(m)].push(particle);
                    }
                    else {
                        // Particle isn't visible, but it still moves through the field.
                        particle.x = xt;
                        particle.y = yt;
                    }
                }
                particle.age += 1;
            });
        }

        var g = d3.select("#animation").node().getContext("2d");
        g.lineWidth = PARTICLE_LINE_WIDTH;
        g.fillStyle = fadeFillStyle;

        function draw() {
            // Fade existing particle trails.
            var prev = g.globalCompositeOperation;
            g.globalCompositeOperation = "destination-in";
            g.fillRect(bounds.x, bounds.y, bounds.width, bounds.height);
            g.globalCompositeOperation = prev;

            // Draw new particle trails.
            buckets.forEach(function(bucket, i) {
                if (bucket.length > 0) {
                    g.beginPath();
                    g.strokeStyle = colorStyles[i];
                    bucket.forEach(function(particle) {
                        g.moveTo(particle.x, particle.y);
                        g.lineTo(particle.xt, particle.yt);
                        particle.x = particle.xt;
                        particle.y = particle.yt;
                    });
                    g.stroke();
                }
            });
        }

        (function frame() {
            try {
                if (cancel.requested) {
                    field.release();
                    return;
                }
                evolve();
                draw();
                setTimeout(frame, FRAME_RATE);
            }
            catch (e) {
                report.error(e);
            }
        })();
    }

    function drawGridPoints(ctx, grid, globe) {
        if (!grid || !globe || !configuration.get("showGridPoints")) return;

        ctx.fillStyle = "rgba(255, 255, 255, 1)";
        // Use the clipping behavior of a projection stream to quickly draw visible points.
        var stream = globe.projection.stream({
            point: function(x, y) {
                ctx.fillRect(Math.round(x), Math.round(y), 1, 1);
            }
        });
        grid.forEachPoint(function(λ, φ, d) {
            if (µ.isValue(d)) {
                stream.point(λ, φ);
            }
        });
    }

    /*
function resolveScaleTarget(grids, overlayType) {
    if (!grids) return null;
    var hasDistinctOverlay =
        grids.overlayGrid &&
        grids.overlayGrid !== grids.primaryGrid &&
        overlayType &&
        overlayType !== "off" &&
        overlayType !== "default";

    if (hasDistinctOverlay) {
        return {
            product: grids.overlayGrid,
            isVectorPrimary: false
        };
    }

    if (grids.primaryGrid) {
        return {
            product: grids.primaryGrid,
            isVectorPrimary: grids.primaryGrid.field === "vector"
        };
    }

    return null;
}


function drawOverlay(field, overlayType) {
    if (!field) return;

    var ctx = d3.select("#overlay").node().getContext("2d"), grid = (gridAgent.value() || {}).overlayGrid;

    µ.clearCanvas(d3.select("#overlay").node());
    µ.clearCanvas(d3.select("#scale").node());
    if (overlayType) {
        if (overlayType !== "off") {
            ctx.putImageData(field.overlay, 0, 0);
        }
        drawGridPoints(ctx, grid, globeAgent.value());
    }

    if (grid) {
        // Draw color bar for reference.
        var colorBar = d3.select("#scale"), scale = grid.scale, bounds = scale.bounds;
        var c = colorBar.node(), g = c.getContext("2d"), n = c.width - 1;
        for (var i = 0; i <= n; i++) {
            var rgb = scale.gradient( µ.spread(i / n, bounds[0], bounds[1]) , 1);
            g.fillStyle = "rgb(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ")";
            g.fillRect(i, 0, 1, c.height);
        }

        // 커스텀 툴팁 요소 생성 (없으면 생성)
        var tooltip = d3.select("#scale-tooltip");
        if (tooltip.empty()) {
            tooltip = d3.select("body").append("div")
                .attr("id", "scale-tooltip")
                .style("position", "absolute")
                .style("padding", "4px 8px")
                .style("background", "rgba(0, 0, 0, 0.75)")
                .style("color", "#fff")
                .style("border-radius", "3px")
                .style("font-size", "11px")
                .style("pointer-events", "none")
                .style("display", "none")
                .style("z-index", "1000");
        }

        // Show custom tooltip on hover.
        colorBar.on("mousemove", function() {
            var x = d3.mouse(this)[0];
            var actualWidth = this.offsetWidth || c.width;
            var pct = µ.clamp(x / actualWidth, 0, 1);
            var value = µ.spread(pct, bounds[0], bounds[1]);
            var elementId = grid.type === "wind" ? "#location-wind-units" : "#location-value-units";
            var units = createUnitToggle(elementId, grid).value();

            tooltip
                .style("display", "block")
                .style("left", (d3.event.pageX + 10) + "px")
                .style("top", (d3.event.pageY - 25) + "px")
                .text(µ.formatScalar(value, units) + " " + units.label);
        });
        colorBar.on("mouseout", function() {
            tooltip.style("display", "none");
        });
    }
}
*/
    function drawOverlay(field, overlayType) {
        if (!field) return;

        var grids = gridAgent.value() || {};
        var ctx = d3.select("#overlay").node().getContext("2d");
        var overlayGrid = grids.overlayGrid;

        µ.clearCanvas(d3.select("#overlay").node());
        µ.clearCanvas(d3.select("#scale").node());

        if (overlayType) {
            if (overlayType !== "off") {
                ctx.putImageData(field.overlay, 0, 0);
            }
            drawGridPoints(ctx, overlayGrid, globeAgent.value());
        }

        var hasDistinctOverlay =
            overlayGrid &&
            overlayGrid !== grids.primaryGrid &&
            overlayType &&
            overlayType !== "off" &&
            overlayType !== "default";

        var scaleGrid = hasDistinctOverlay ? overlayGrid : grids.primaryGrid;

        if (scaleGrid && scaleGrid.scale) {
            var colorBar = d3.select("#scale");
            var c = colorBar.node();

            // 메뉴가 늦게 열려 width가 0이었던 경우 보정
            if (!c.width && d3.select("#menu").node()) {
                c.width = Math.max(100, Math.floor(d3.select("#menu").node().offsetWidth * 0.97));
            }

            var scale = scaleGrid.scale;
            var bounds = scale.bounds;
            var g = c.getContext("2d");
            var n = c.width - 1;

            for (var i = 0; i <= n; i++) {
                var v = µ.spread(i / n, bounds[0], bounds[1]);
                var rgb = scale.gradient(v, 1);
                g.fillStyle = "rgb(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ")";
                g.fillRect(i, 0, 1, c.height);
            }

            // 커스텀 툴팁 요소 생성 (없으면 생성)
            var tooltip = d3.select("#scale-tooltip");
            if (tooltip.empty()) {
                tooltip = d3.select("body").append("div")
                    .attr("id", "scale-tooltip")
                    .style("position", "absolute")
                    .style("padding", "4px 8px")
                    .style("background", "rgba(0, 0, 0, 0.75)")
                    .style("color", "#fff")
                    .style("border-radius", "3px")
                    .style("font-size", "11px")
                    .style("pointer-events", "none")
                    .style("display", "none")
                    .style("z-index", "1000");
            }

            // Show custom tooltip on hover.
            colorBar.on("mousemove", function() {
                var x = d3.mouse(this)[0];
                var actualWidth = this.offsetWidth || c.width;
                var pct = µ.clamp(x / actualWidth, 0, 1);
                var value = µ.spread(pct, bounds[0], bounds[1]);


                // 수정: target 대신 hasDistinctOverlay와 grids 상태를 직접 확인하거나
                // 아래와 같이 scaleGrid의 속성을 활용합니다.
                var isVector = scaleGrid.field === "vector";
                var elementId = isVector ? "#location-wind-units" : "#location-value-units";

                // 수정: scaleProduct 대신 scaleGrid를 사용
                var units = createUnitToggle(elementId, scaleGrid).value();

                tooltip
                    .style("display", "block")
                    .style("left", (d3.event.pageX + 10) + "px")
                    .style("top", (d3.event.pageY - 25) + "px")
                    .text(µ.formatScalar(value, units) + (units.label ? " " + units.label : ""));
            });
            colorBar.on("mouseout", function() {
                tooltip.style("display", "none");
            });
        }
    }


    function validityDate(grids) {
        var THREE_HOURS = 3 * HOUR;
        var configDate = configuration.get("date");
        var parts = configDate.split("/");
        var hhmm = configuration.get("hour");

        if (grids && grids.primaryGrid) {
            var primaryGrid = grids.primaryGrid;

            if (configDate === "current" && primaryGrid.refTime) {
                return primaryGrid.refTime.getTime();
            }

            if (primaryGrid.date) {
                return primaryGrid.date.getTime();
            }
        }

        var now = Math.floor(Date.now() / THREE_HOURS) * THREE_HOURS;

        return parts.length > 1 ?
            Date.UTC(+parts[0], parts[1] - 1, +parts[2], +hhmm.substring(0, 2)) :
            parts[0] === "current" ? now : null;
    }

    function showDate(grids) {
        var date = new Date(validityDate(grids)), isLocal = d3.select("#data-date").classed("local");
        var formatted = isLocal ? µ.toLocalISO(date) : µ.toUTCISO(date);
        d3.select("#data-date").text(formatted + " " + (isLocal ? "Local" : "UTC"));
        d3.select("#toggle-zone").text("⇄ " + (isLocal ? "UTC" : "Local"));
    }




    function showGridDetails(grids) {
        showDate(grids);
        var description = "", center = "";
        if (grids) {
            var langCode = d3.select("body").attr("data-lang") || "en";
            var pd = grids.primaryGrid.description(langCode), od = grids.overlayGrid.description(langCode);
            description = od.name + od.qualifier;
            if (grids.primaryGrid !== grids.overlayGrid) {
                // Combine both grid descriptions together with a " + " if their qualifiers are the same.
                description = (pd.qualifier === od.qualifier ? pd.name : pd.name + pd.qualifier) + " + " + description;
            }
            center = grids.overlayGrid.source;
        }
        d3.select("#data-layer").text(description);
        d3.select("#data-center").text(center);
    }

    function createUnitToggle(id, product) {
        var units = product.units, size = units.length;
        var index = +(d3.select(id).attr("data-index") || 0) % size;
        return {
            value: function() {
                return units[index];
            },
            next: function() {
                d3.select(id).attr("data-index", index = ((index + 1) % size));
            }
        };
    }

    function showWindAtLocation(wind, product) {
        var unitToggle = createUnitToggle("#location-wind-units", product), units = unitToggle.value();
        d3.select("#location-wind").text(µ.formatVector(wind, units));
        d3.select("#location-wind-units").text(units.label).on("click", function() {
            unitToggle.next();
            showWindAtLocation(wind, product);
        });
    }


    function showOverlayValueAtLocation(value, product) {
        var unitToggle = createUnitToggle("#location-value-units", product), units = unitToggle.value();
        d3.select("#location-value").text(µ.formatScalar(value, units));
        d3.select("#location-value-units").text(units.label).on("click", function() {
            unitToggle.next();
            showOverlayValueAtLocation(value, product);
        });
    }

    // Stores the point and coordinate of the currently visible location. This is used to update the location
    // details when the field changes.
    var activeLocation = {};

    function showLocationDetails(point, coord) {
        point = point || [];
        coord = coord || [];
        var grids = gridAgent.value(), field = fieldAgent.value(), λ = coord[0], φ = coord[1];
        if (!field || !field.isInsideBoundary(point[0], point[1])) {
            return;
        }

        clearLocationDetails(false);  // clean the slate
        activeLocation = {point: point, coord: coord};  // remember where the current location is

        if (_.isFinite(λ) && _.isFinite(φ)) {
            d3.select("#location-coord").text(µ.formatCoordinates(λ, φ));
            d3.select("#location-close").classed("invisible", false);
        }

        if (field.isDefined(point[0], point[1]) && grids) {
            var wind = grids.primaryGrid.interpolate(λ, φ);
            if (µ.isValue(wind)) {
                showWindAtLocation(wind, grids.primaryGrid);
            }
            if (grids.overlayGrid !== grids.primaryGrid) {
                var value = grids.overlayGrid.interpolate(λ, φ);
                if (µ.isValue(value)) {
                    showOverlayValueAtLocation(value, grids.overlayGrid);
                }
            }
        }
    }

    function updateLocationDetails() {
        showLocationDetails(activeLocation.point, activeLocation.coord);
    }

    function clearLocationDetails(clearEverything) {
        d3.select("#location-coord").text("");
        d3.select("#location-close").classed("invisible", true);
        d3.select("#location-wind").text("");
        d3.select("#location-wind-units").text("");
        d3.select("#location-value").text("");
        d3.select("#location-value-units").text("");
        if (clearEverything) {
            activeLocation = {};
            d3.select(".location-mark").remove();
        }
    }

    function stopCurrentAnimation(alsoClearCanvas) {
        animatorAgent.cancel();
        if (alsoClearCanvas) {
            µ.clearCanvas(d3.select("#animation").node());
        }
    }

    function bindButtonToConfiguration(elementId, newAttr, keys) {
        keys = keys || _.keys(newAttr);
        d3.select(elementId).on("click", function() {
            if (d3.select(elementId).classed("disabled")) return;
            configuration.save(newAttr);
        });
        configuration.on("change", function(model) {
            var attr = model.attributes;
            d3.select(elementId).classed("highlighted", _.isEqual(_.pick(attr, keys), _.pick(newAttr, keys)));
        });
    }

    function reportSponsorClick(type) {
        if (ga) {
            ga("send", "event", "sponsor", type);
        }
    }


    function init() {
        report.status("Initializing...");

        d3.selectAll(".fill-screen").attr("width", view.width).attr("height", view.height);
        // Adjust size of the scale canvas to fill the width of the menu to the right of the label.

       // var label = d3.select("#scale-label").node();
        d3.select("#scale")
            //.attr("width", (d3.select("#menu").node().offsetWidth - label.offsetWidth) * 0.97)
            //.attr("height", label.offsetHeight / 2);
            //  class menu-row-column 적용
            .attr("width", (d3.select("#menu").node().offsetWidth ) * 0.97)


        d3.select("#show-menu").on("click", function() {
            d3.select("#menu").classed("invisible", !d3.select("#menu").classed("invisible"));
            /*
            if (µ.isEmbeddedInIFrame()) {
                window.open("http://earth.nullschool.net/" + window.location.hash, "_blank");
            } else {
                d3.select("#menu").classed("invisible", !d3.select("#menu").classed("invisible"));
            }
            */
        });

        if (µ.isFF()) {
            // Workaround FF performance issue of slow click behavior on map having thick coastlines.
            d3.select("#display").classed("firefox", true);
        }

        // Tweak document to distinguish CSS styling between touch and non-touch environments. Hacky hack.
        if ("ontouchstart" in document.documentElement) {
            d3.select(document).on("touchstart", function() {});  // this hack enables :active pseudoclass
        }
        else {
            d3.select(document.documentElement).classed("no-touch", true);  // to filter styles problematic for touch
        }

        // Bind configuration to URL bar changes.
        d3.select(window).on("hashchange", function() {
            log.debug("hashchange");
            configuration.fetch({trigger: "hashchange"});
        });

        configuration.on("change", report.reset);

        meshAgent.listenTo(configuration, "change:topology", function(context, attr) {
            meshAgent.submit(buildMesh, attr);
        });

        globeAgent.listenTo(configuration, "change:projection", function(source, attr) {
            globeAgent.submit(buildGlobe, attr);
        });

        gridAgent.listenTo(configuration, "change", function() {
            var changed = _.keys(configuration.changedAttributes()), rebuildRequired = false;

            // Build a new grid if any layer-related attributes have changed.
            if (_.intersection(changed, ["date", "hour", "param", "surface", "level"]).length > 0) {
                rebuildRequired = true;
            }
            // Build a new grid if the new overlay type is different from the current one.
            var overlayType = configuration.get("overlayType") || "default";
            if (_.indexOf(changed, "overlayType") >= 0 && overlayType !== "off") {
                var grids = (gridAgent.value() || {}), primary = grids.primaryGrid, overlay = grids.overlayGrid;
                if (!overlay) {
                    // Do a rebuild if we have no overlay grid.
                    rebuildRequired = true;
                }
                else if (overlay.type !== overlayType && !(overlayType === "default" && primary === overlay)) {
                    // Do a rebuild if the types are different.
                    rebuildRequired = true;
                }
            }

            if (rebuildRequired) {
                gridAgent.submit(buildGrids);
            }
        });
        gridAgent.on("submit", function() {
            showGridDetails(null);
        });
        gridAgent.on("update", function(grids) {
            showGridDetails(grids);
        });
        d3.select("#toggle-zone").on("click", function() {
            d3.select("#data-date").classed("local", !d3.select("#data-date").classed("local"));
            showDate(gridAgent.cancel.requested ? null : gridAgent.value());
        });

        function startRendering() {
            rendererAgent.submit(buildRenderer, meshAgent.value(), globeAgent.value());
        }
        rendererAgent.listenTo(meshAgent, "update", startRendering);
        rendererAgent.listenTo(globeAgent, "update", startRendering);

        function startInterpolation() {
            fieldAgent.submit(interpolateField, globeAgent.value(), gridAgent.value());
        }
        function cancelInterpolation() {
            fieldAgent.cancel();
        }
        fieldAgent.listenTo(gridAgent, "update", startInterpolation);
        fieldAgent.listenTo(rendererAgent, "render", startInterpolation);
        fieldAgent.listenTo(rendererAgent, "start", cancelInterpolation);
        fieldAgent.listenTo(rendererAgent, "redraw", cancelInterpolation);

        animatorAgent.listenTo(fieldAgent, "update", function(field) {
            animatorAgent.submit(animate, globeAgent.value(), field, gridAgent.value());
        });
        animatorAgent.listenTo(rendererAgent, "start", stopCurrentAnimation.bind(null, true));
        animatorAgent.listenTo(gridAgent, "submit", stopCurrentAnimation.bind(null, false));
        animatorAgent.listenTo(fieldAgent, "submit", stopCurrentAnimation.bind(null, false));

        overlayAgent.listenTo(fieldAgent, "update", function() {
            overlayAgent.submit(drawOverlay, fieldAgent.value(), configuration.get("overlayType"));
        });
        overlayAgent.listenTo(rendererAgent, "start", function() {
            overlayAgent.submit(drawOverlay, fieldAgent.value(), null);
        });
        overlayAgent.listenTo(configuration, "change", function() {
            var changed = _.keys(configuration.changedAttributes())
            // if only overlay relevant flags have changed...
            if (_.intersection(changed, ["overlayType", "showGridPoints"]).length > 0) {
                overlayAgent.submit(drawOverlay, fieldAgent.value(), configuration.get("overlayType"));
            }
        });

        // Add event handlers for showing, updating, and removing location details.
        inputController.on("click", showLocationDetails);
        fieldAgent.on("update", updateLocationDetails);
        d3.select("#location-close").on("click", _.partial(clearLocationDetails, true));

        // Modify menu depending on what mode we're in.
        configuration.on("change:param", function(context, mode) {
            d3.selectAll(".ocean-mode").classed("invisible", mode !== "ocean");
            d3.selectAll(".wind-mode").classed("invisible", mode !== "wind");
            d3.selectAll(".ice-mode").classed("invisible", mode !== "ice");
            d3.select("#nav-backward-more").attr("title", "-1 Day");
            d3.select("#nav-backward").attr("title", "-3 Hours");
            d3.select("#nav-forward").attr("title", "+3 Hours");
            d3.select("#nav-forward-more").attr("title", "+1 Day");
        });


        d3.select("#mode-select").on("change", function(event) {
            // 1. 새로 선택된 값 가져오기
            const selectedValue = this.value; // 또는 event.target.value

            console.log("선택된 모드:", selectedValue); // "Air" 또는 "Ocean"출력

            // 2. 값에 따른 시각화 업데이트 로직 수행
            if (selectedValue === "Air") {
                if (configuration.get("param") !== "wind") {
                    stopCurrentAnimation(true);
                    var wind = {param: "wind", surface: "surface", level: "level", overlayType: "default",  date:"current"}
                    configuration.save(wind);
                }
            } else if (selectedValue === "Ocean") {
                if (configuration.get("param") !== "ocean") {
                    stopCurrentAnimation(true);
                    var ocean = {param: "ocean", surface: "surface", level: "currents", overlayType: "default",  date:"current"};
                    configuration.save(ocean);
/*
                    var attr = _.clone(configuration.attributes);
                    if (attr.date === "current") {
                        configuration.save(ocean);
                    } else {
                        when.all(products.productsFor(_.extend(attr, ocean))).spread(function(product) {
                            if (product.date) {
                                configuration.save( _.extend( ocean, µ.dateToConfig(product.date, "ocean" ) ) );
                            }
                        }).otherwise(report.error);
                    }
                    stopCurrentAnimation(true);  // cleanup particle artifacts over continents
 */
                }
            } else if (selectedValue === "Ice") {
                if (configuration.get("param") !== "ice") {
                    stopCurrentAnimation(true);
                    var ice = { param: "ice", surface: "surface", level: "level", overlayType: "default", date:"current" };
                    configuration.save(ice);
                }
            }
        });

        // Keep mode-select synced with configuration.get("param")
        configuration.on("change:param", function(x, param) {
            var selectNode = d3.select("#mode-select").node();
            if (selectNode) {
                if(param === "wind" ){
                    selectNode.value = "Air";
                    d3.select("#overlay-select").node().value = "WindSpeed";
                }else if(param === "ocean" ){
                    selectNode.value = "Ocean";
                    d3.select("#overlay-select-ocean").node().value = "Currents";
                }else if(param === "ice" ){
                    selectNode.value = "Ice";
                    d3.select("#overlay-select-ice").node().value = "IceCurrents";
                }
            }
        });

        // Add logic to disable buttons that are incompatible with each other.
        configuration.on("change:overlayType", function(x, ot) {
            d3.select("#surface-level").classed("disabled", ot === "air_density" || ot === "wind_power_density");
        });
        configuration.on("change:surface", function(x, s) {
            d3.select("#overlay-air_density").classed("disabled", s === "surface");
            d3.select("#overlay-wind_power_density").classed("disabled", s === "surface");
        });

        // Add event handlers for the time navigation buttons.
        d3.select("#nav-backward-more").on("click", navigate.bind(null, -10));
        d3.select("#nav-forward-more" ).on("click", navigate.bind(null, +10));
        d3.select("#nav-backward"     ).on("click", navigate.bind(null, -1));
        d3.select("#nav-forward"      ).on("click", navigate.bind(null, +1));
        d3.select("#nav-now").on("click", function() { configuration.save({date: "current", hour: ""}); });

        d3.select("#option-show-grid").on("click", function() {
            configuration.save({showGridPoints: !configuration.get("showGridPoints")});
        });
        configuration.on("change:showGridPoints", function(x, showGridPoints) {
            d3.select("#option-show-grid").classed("highlighted", showGridPoints);
        });

        // Height select element event handler and configuration listener
        d3.select("#height-select").on("change", function() {
            var selectedOption = this.options[this.selectedIndex];
            var id = selectedOption.id; // e.g., "surface-level", "isobaric-1000hPa"
            var parts = id.split("-");
            configuration.save({param: "wind", surface: parts[0], level: parts[1]});
        });

        configuration.on("change", function(model) {
            var surface = model.get("surface");
            var level = model.get("level");
            var targetId = level ? surface + "-" + level : surface;

            var selectNode = d3.select("#height-select").node();
            if (selectNode) {
                for (var i = 0; i < selectNode.options.length; i++) {
                    if (selectNode.options[i].id === targetId) {
                        selectNode.selectedIndex = i;
                        break;
                    }
                }
            }
        });
        // Overlay select element event handler and configuration listener
        d3.select("#overlay-select").on("change", function() {
            var value = this.value;
            switch (value) {
                case "None":
                    configuration.save({overlayType: "off"});
                    break;
                case "WindSpeed":
                    configuration.save({param: "wind", overlayType: "default"});
                    break;
                case "Temperature":
                    configuration.save({overlayType: "temperature"});
                    break;
                case "RelativeHumidity":
                    configuration.save({overlayType: "relative_humidity"});
                    break;
            }
        });

        configuration.on("change:overlayType", function(model, overlayType) {
            var selectNode = d3.select("#overlay-select").node();
            if (!selectNode) return;

            var value = "None";
            if (overlayType === "default") {
                value = "WindSpeed";
            } else if (overlayType === "temperature") {
                value = "Temperature";
            } else if (overlayType === "relative_humidity") {
                value = "RelativeHumidity";
            } else if (overlayType === "off") {
                value = "None";
            } else {
                value = overlayType;
            }

            for (var i = 0; i < selectNode.options.length; i++) {
                if (selectNode.options[i].value === value) {
                    selectNode.selectedIndex = i;
                    break;
                }
            }
        });


        d3.select("#animate-select").on("change", function() {
            var value = this.value;
            switch (value) {
                case "Currents":
                    configuration.save({param: "ocean", surface: "surface", level: "currents"});
                    break;
                case "IceCurrents":
                    configuration.save({param: "ice", surface: "surface", level: "currents"});
                    break;
            }
        });

        configuration.on("change", function() {
            var selectNode = d3.select("#animate-select").node();
            if (!selectNode) return;

            var value = "Currents";

            for (var i = 0; i < selectNode.options.length; i++) {
                if (selectNode.options[i].value === value) {
                    selectNode.selectedIndex = i;
                    break;
                }
            }
        });


        d3.select("#overlay-select-ocean").on("change", function() {
            var value = this.value;
            switch (value) {
                case "None":
                    configuration.save({overlayType: "off"});
                    break;
                case "Currents":
                    configuration.save({param: "ocean",overlayType: "default"});
                    break;
                case "SeaSurfaceTemperature":
                    configuration.save({overlayType: "prog_temperature"});
                    break;
                case "SeaSurfaceSalinity":
                    configuration.save({overlayType: "prog_salinity"});
                    break;
            }
        });

        configuration.on("change;overlayType", function(overlayType) {
            var selectNode = d3.select("#overlay-select-ocean").node();
            if (!selectNode) return;

            var value = "None";
            if (overlayType === "off") {
                value = "None";
            } else if (overlayType === "default") {
                value = "Currents";
            } else if (overlayType === "prog_temperature") {
                value = "SeaSurfaceTemperature";
            } else if (overlayType === "prog_salinity") {
                value = "SeaSurfaceSalinity";
            } else {
                value = overlayType;
            }

            for (var i = 0; i < selectNode.options.length; i++) {
                if (selectNode.options[i].value === value) {
                    selectNode.selectedIndex = i;
                    break;
                }
            }
        });



        d3.select("#overlay-select-ice").on("change", function() {
            var value = this.value;
            switch (value) {
                case "None":
                    configuration.save({overlayType: "off"});
                    break;
                case "IceCurrents":
                    configuration.save({param: "ice",overlayType: "default"});
                    break;
                case "IceCoverage":
                    configuration.save({overlayType: "ice_coverage"});
                    break;
                case "IceTemperature":
                    configuration.save({overlayType: "ice_temperature"});
                    break;
                case "IceThickness":
                    configuration.save({overlayType: "ice_thickness"});
                    break;
            }
        });

        configuration.on("change;overlayType", function(overlayType) {
            var selectNode = d3.select("#overlay-select-ice").node();
            if (!selectNode) return;

            var value = "None";
            if (overlayType === "off") {
                value = "None";
            } else if (overlayType === "default") {
                value = "IceCurrents";
            } else if (overlayType === "ice_coverage") {
                value = "IceCoverage";
            } else if (overlayType === "ice_temperature") {
                value = "IceTemperature";
            } else if (overlayType === "ice_thickness") {
                value = "IceThickness";
            } else {
                value = overlayType;
            }

            for (var i = 0; i < selectNode.options.length; i++) {
                if (selectNode.options[i].value === value) {
                    selectNode.selectedIndex = i;
                    break;
                }
            }
        });

        // Projection select element event handler and configuration listener
        var projectionMap = {
            "A": "atlantis",
            "AE": "azimuthal_equidistant",
            "CE": "conic_equidistant",
            "E": "equirectangular",
            "O": "orthographic",
            "S": "stereographic",
            "WB": "waterman",
            "W3": "winkel3"
        };

        var reverseProjectionMap = {
            "atlantis": "A",
            "azimuthal_equidistant": "AE",
            "conic_equidistant": "CE",
            "equirectangular": "E",
            "orthographic": "O",
            "stereographic": "S",
            "waterman": "WB",
            "winkel3": "W3"
        };

        d3.select("#projection-select").on("change", function() {
            var value = this.value;
            var projKey = projectionMap[value] || value;
            configuration.save({projection: projKey, orientation: ""});
        });

        configuration.on("change:projection", function(model, proj) {
            var selectNode = d3.select("#projection-select").node();
            if (!selectNode) return;

            var targetValue = reverseProjectionMap[proj] || proj;
            for (var i = 0; i < selectNode.options.length; i++) {
                if (selectNode.options[i].value === targetValue) {
                    selectNode.selectedIndex = i;
                    break;
                }
            }
        });


        // When touch device changes between portrait and landscape, rebuild globe using the new view size.
        d3.select(window).on("orientationchange", function() {
            view = µ.view();
            globeAgent.submit(buildGlobe, configuration.get("projection"));
        });


        // 창 크기 변경(resize) 시 지도 및 Canvas 크기 자동 재설정
        d3.select(window).on("resize", _.debounce(function() {
            view = µ.view();
            d3.selectAll(".fill-screen")
                .attr("width", view.width)
                .attr("height", view.height);
            globeAgent.submit(buildGlobe, configuration.get("projection"));
        }, 200));

    }

    function start() {
        // Everything is now set up, so load configuration from the hash fragment and kick off change events.
        configuration.fetch();
    }

    when(true).then(init).then(start).otherwise(report.error);

})();
