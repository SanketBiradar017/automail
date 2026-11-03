/* AutoMail hero globe: realistic Earth (NASA-derived textures) with country
 * borders, labels, and envelopes travelling between cities.
 * Three.js (vendored r128) + assets are loaded lazily when the hero is near view.
 * Honors prefers-reduced-motion (one static frame, no render loop). */

(function () {
    "use strict";

    const ASSETS = {
        day: "/static/textures/earth_atmos_2048.jpg",
        spec: "/static/textures/earth_specular_2048.jpg",
        clouds: "/static/textures/earth_clouds_1024.png",
        borders: "/static/data/borders.json",
    };

    // [name, lat, lon]
    const CITIES = [
        ["San Francisco", 37.77, -122.42], ["New York", 40.71, -74.0], ["Sao Paulo", -23.55, -46.63],
        ["London", 51.5, -0.12], ["Lagos", 6.52, 3.38], ["Dubai", 25.2, 55.27],
        ["Bengaluru", 12.97, 77.59], ["Singapore", 1.35, 103.82], ["Tokyo", 35.68, 139.69],
        ["Sydney", -33.87, 151.2], ["Johannesburg", -26.2, 28.04], ["Berlin", 52.52, 13.4],
    ];
    const ROUTES = [[0,1],[1,3],[3,5],[5,6],[6,7],[7,8],[8,0],[3,4],[4,2],[2,1],[6,9],[3,11],[10,5],[9,8],[0,2],[11,6]];

    // Subtle country labels: [text, lat, lon]
    const LABELS = [
        ["United States", 39, -98], ["Canada", 58, -106], ["Brazil", -10, -52], ["Argentina", -38, -65],
        ["United Kingdom", 54, -2.5], ["France", 46.5, 2.5], ["Germany", 51, 10.3], ["Nigeria", 9, 8],
        ["South Africa", -29, 24], ["Egypt", 26.5, 30], ["Saudi Arabia", 24, 44.5], ["Russia", 61, 95],
        ["India", 22, 79], ["China", 35, 103], ["Japan", 36.5, 138.5], ["Australia", -25, 134],
        ["Indonesia", -2, 115], ["Mexico", 24, -102],
    ];

    const stage = document.getElementById("globeStage");
    if (!stage) return;
    const reduceQuery = matchMedia("(prefers-reduced-motion: reduce)");

    function hasWebGL() {
        try {
            const c = document.createElement("canvas");
            return !!(window.WebGLRenderingContext && (c.getContext("webgl") || c.getContext("experimental-webgl")));
        } catch (e) { return false; }
    }

    function loadThree() {
        if (window.THREE) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = "/static/vendor/three.r128.min.js";
            s.onload = resolve; s.onerror = reject;
            document.head.appendChild(s);
        });
    }

    function loadAssets(T, renderer) {
        const loader = new T.TextureLoader();
        const maxAniso = Math.min(4, renderer.capabilities.getMaxAnisotropy());
        const tex = (url, srgb) => new Promise((res, rej) => loader.load(url, (t) => {
            t.anisotropy = maxAniso;
            if (srgb) t.encoding = T.sRGBEncoding;
            res(t);
        }, undefined, rej));
        return Promise.all([
            tex(ASSETS.day, true), tex(ASSETS.spec, false), tex(ASSETS.clouds, true),
            fetch(ASSETS.borders).then(r => r.json()),
        ]).then(([day, spec, clouds, borders]) => ({ day, spec, clouds, borders }));
    }

    function init() {
        const T = THREE;
        const renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
        renderer.setClearColor(0x000000, 0);
        renderer.outputEncoding = T.sRGBEncoding;

        loadAssets(T, renderer).then((assets) => build(T, renderer, assets)).catch(() => { stage.hidden = true; });
    }

    function build(T, renderer, A) {
        stage.appendChild(renderer.domElement);

        const scene = new T.Scene();
        const camera = new T.PerspectiveCamera(35, 1, 0.1, 50);
        camera.position.z = 4.3;

        // Soft, natural light: warm sun from upper-left + cool sky fill. Fixed to the
        // camera frame, so the day/night terminator stays put while the Earth turns.
        const sun = new T.DirectionalLight(0xfff4e0, 1.25);
        sun.position.set(-3.2, 1.6, 3.6);
        scene.add(sun, new T.AmbientLight(0xa9bbd8, 0.58));

        const root = new T.Group(), globe = new T.Group(), cloudsGroup = new T.Group();
        root.add(globe, cloudsGroup); scene.add(root);
        const TILT = 0.38;
        globe.rotation.x = TILT; cloudsGroup.rotation.x = TILT;
        const faceLon = (lon) => -(lon + 90) * Math.PI / 180;     // spin that centres a longitude
        globe.rotation.y = faceLon(15);

        const ll = (lat, lon, r = 1) => {
            const phi = (90 - lat) * Math.PI / 180, th = (lon + 180) * Math.PI / 180;
            return new T.Vector3(-r * Math.sin(phi) * Math.cos(th), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(th));
        };

        /* ---- Earth ---- */
        globe.add(new T.Mesh(
            new T.SphereGeometry(1, 96, 64),
            new T.MeshPhongMaterial({ map: A.day, specularMap: A.spec, specular: new T.Color(0x2a3442), shininess: 16 })
        ));

        /* ---- clouds (thin, semi-transparent) ---- */
        cloudsGroup.add(new T.Mesh(
            new T.SphereGeometry(1.009, 64, 48),
            new T.MeshPhongMaterial({ map: A.clouds, transparent: true, opacity: 0.42, depthWrite: false })
        ));

        /* ---- atmosphere: thin natural blue rim ---- */
        scene.add(new T.Mesh(
            new T.SphereGeometry(1.045, 64, 48),
            new T.ShaderMaterial({
                transparent: true, side: T.BackSide, depthWrite: false,
                uniforms: { uColor: { value: new T.Color(0x78a9ee) } },
                vertexShader: "varying vec3 vN; void main(){ vN=normalize(normalMatrix*normal); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
                fragmentShader: "uniform vec3 uColor; varying vec3 vN; void main(){ float i=pow(clamp(.62-dot(vN,vec3(0.,0.,1.)),0.,1.),3.4); gl_FragColor=vec4(uColor,clamp(i*1.05,0.,.34)); }",
            })
        ));

        /* ---- country borders ---- */
        const seg = [];
        A.borders.forEach((arc) => {
            for (let i = 0; i + 3 < arc.length; i += 2) {
                const a = ll(arc[i + 1], arc[i], 1.0015), b = ll(arc[i + 3], arc[i + 2], 1.0015);
                seg.push(a.x, a.y, a.z, b.x, b.y, b.z);
            }
        });
        const bGeo = new T.BufferGeometry();
        bGeo.setAttribute("position", new T.Float32BufferAttribute(seg, 3));
        globe.add(new T.LineSegments(bGeo, new T.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.42, depthWrite: false })));

        /* ---- city markers: small clean dots ---- */
        const cityVec = CITIES.map(c => ll(c[1], c[2], 1.006));
        const cp = new Float32Array(cityVec.length * 3);
        cityVec.forEach((v, i) => cp.set([v.x, v.y, v.z], i * 3));
        const cGeo = new T.BufferGeometry();
        cGeo.setAttribute("position", new T.BufferAttribute(cp, 3));
        globe.add(new T.Points(cGeo, new T.ShaderMaterial({
            transparent: true, depthWrite: false,
            uniforms: { uPR: { value: renderer.getPixelRatio() } },
            vertexShader: "uniform float uPR; void main(){ vec4 mv=modelViewMatrix*vec4(position,1.); gl_PointSize=8.*uPR*(4.3/-mv.z); gl_Position=projectionMatrix*mv; }",
            fragmentShader: "void main(){ float d=length(gl_PointCoord-.5); float ring=smoothstep(.5,.44,d); float core=smoothstep(.3,.26,d); vec3 c=mix(vec3(.04,.1,.23),vec3(1.),core); gl_FragColor=vec4(c,ring*.95); }",
        })));

        /* ---- routes: thin, curved, soft white ---- */
        function arcPoint(a, b, t, lift) {
            const w = Math.acos(Math.min(1, a.dot(b))), s = Math.sin(w) || 1;
            const p = a.clone().multiplyScalar(Math.sin((1 - t) * w) / s).add(b.clone().multiplyScalar(Math.sin(t * w) / s));
            return p.normalize().multiplyScalar(1.008 + lift * Math.sin(Math.PI * t));
        }
        const routes = ROUTES.map(([i, j]) => {
            const a = cityVec[i].clone().normalize(), b = cityVec[j].clone().normalize();
            const lift = 0.07 + 0.2 * (Math.acos(Math.min(1, a.dot(b))) / Math.PI);
            const pts = []; for (let k = 0; k <= 56; k++) pts.push(arcPoint(a, b, k / 56, lift));
            globe.add(new T.Line(new T.BufferGeometry().setFromPoints(pts),
                new T.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.5, depthWrite: false })));
            return { a, b, lift };
        });

        /* ---- country labels (fade when facing away) ---- */
        const labels = LABELS.map(([text, lat, lon]) => {
            const c = document.createElement("canvas"), g = c.getContext("2d");
            const fs = 34; g.font = `600 ${fs}px Inter, "Segoe UI", Arial, sans-serif`;
            const w = Math.ceil(g.measureText(text).width) + 24; c.width = w; c.height = fs + 20;
            g.font = `600 ${fs}px Inter, "Segoe UI", Arial, sans-serif`;
            g.textBaseline = "middle"; g.textAlign = "center";
            g.shadowColor = "rgba(0,12,32,.85)"; g.shadowBlur = 8;
            g.fillStyle = "rgba(255,255,255,.95)"; g.fillText(text, w / 2, c.height / 2 + 1);
            const sp = new T.Sprite(new T.SpriteMaterial({ map: new T.CanvasTexture(c), transparent: true, depthWrite: false, opacity: 0 }));
            const h = 0.05; sp.scale.set(h * c.width / c.height, h, 1);
            sp.position.copy(ll(lat, lon, 1.018));
            globe.add(sp);
            return sp;
        });
        const wp = new T.Vector3(), toCam = new T.Vector3();
        function updateLabels() {
            labels.forEach((sp) => {
                sp.getWorldPosition(wp);
                toCam.copy(camera.position).sub(wp).normalize();
                const f = wp.clone().normalize().dot(toCam);
                sp.material.opacity = Math.max(0, Math.min(1, (f - 0.35) / 0.35)) * 0.88;
            });
        }

        /* ---- envelopes: clean cream icon, soft drop shadow, no glow ---- */
        const envTex = (() => {
            const c = document.createElement("canvas"); c.width = 160; c.height = 120;
            const g = c.getContext("2d");
            g.shadowColor = "rgba(0,10,30,.45)"; g.shadowBlur = 10; g.shadowOffsetY = 4;
            g.fillStyle = "#f7f0df"; g.strokeStyle = "#0b1e44"; g.lineWidth = 6; g.lineJoin = "round";
            g.beginPath(); g.roundRect ? g.roundRect(14, 18, 132, 84, 10) : g.rect(14, 18, 132, 84); g.fill();
            g.shadowColor = "transparent"; g.stroke();
            g.beginPath(); g.moveTo(18, 24); g.lineTo(80, 66); g.lineTo(142, 24); g.stroke();
            return new T.CanvasTexture(c);
        })();
        const flyers = [0, 1, 2, 3, 4].map(() => {
            const sp = new T.Sprite(new T.SpriteMaterial({ map: envTex, transparent: true, depthWrite: false, opacity: 0 }));
            sp.scale.set(0.105, 0.079, 1); globe.add(sp);
            return { sp, route: 0, t: 0, dur: 4, wait: 0 };
        });
        function launch(f, first) {
            f.route = (Math.random() * routes.length) | 0;
            f.dur = 4 + Math.random() * 2.5;
            f.t = first ? Math.random() : 0;
            f.wait = first ? 0 : 0.8 + Math.random() * 3;
        }
        flyers.forEach(f => launch(f, true));
        const ease = (t) => t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;

        /* ---- sizing / pointer ---- */
        function resize() {
            const w = stage.clientWidth, h = stage.clientHeight;
            if (!w || !h) return;
            renderer.setSize(w, h, false);
            camera.aspect = w / h; camera.updateProjectionMatrix();
            draw();
        }
        new ResizeObserver(resize).observe(stage);

        const tilt = { x: 0, y: 0, tx: 0, ty: 0 };
        stage.addEventListener("pointermove", (e) => {
            const r = stage.getBoundingClientRect();
            tilt.ty = ((e.clientX - r.left) / r.width - 0.5) * 0.35;
            tilt.tx = ((e.clientY - r.top) / r.height - 0.5) * 0.2;
        });
        stage.addEventListener("pointerleave", () => { tilt.tx = tilt.ty = 0; });

        /* ---- frame loop ---- */
        let last = 0, raf = 0, visible = false;
        function step(dt, animate) {
            if (animate) {
                globe.rotation.y += dt * 0.055;
                cloudsGroup.rotation.y = globe.rotation.y * 1.06;
            }
            tilt.x += (tilt.tx - tilt.x) * 0.05; tilt.y += (tilt.ty - tilt.y) * 0.05;
            root.rotation.x = tilt.x; root.rotation.y = tilt.y;

            flyers.forEach((f) => {
                if (animate) {
                    if (f.wait > 0) f.wait -= dt;
                    else { f.t += dt / f.dur; if (f.t >= 1) launch(f, false); }
                }
                const r = routes[f.route];
                f.sp.position.copy(arcPoint(r.a, r.b, ease(Math.min(f.t, 1)), r.lift + 0.025));
                const o = f.wait > 0 ? 0 : Math.min(1, f.t / 0.1, (1 - f.t) / 0.1);
                f.sp.material.opacity = Math.max(0, o);
            });
        }
        function draw() { updateLabels(); renderer.render(scene, camera); }

        function loop(now) {
            raf = 0;
            if (!visible || document.hidden || reduceQuery.matches) return;
            const dt = Math.min(0.05, (now - last) / 1000 || 0.016); last = now;
            step(dt, true); draw();
            raf = requestAnimationFrame(loop);
        }
        function sync() {
            if (reduceQuery.matches || !visible) {
                if (raf) { cancelAnimationFrame(raf); raf = 0; }
                if (reduceQuery.matches) {
                    globe.rotation.y = faceLon(15); cloudsGroup.rotation.y = globe.rotation.y * 1.06;
                    flyers.forEach((f, i) => { f.t = 0.35 + i * 0.1; f.wait = 0; });
                    step(0, false); draw();
                }
                return;
            }
            if (!raf) { last = performance.now(); raf = requestAnimationFrame(loop); }
        }
        new IntersectionObserver((en) => { visible = en[0].isIntersecting; sync(); }, { threshold: 0.05 }).observe(stage);
        document.addEventListener("visibilitychange", sync);
        reduceQuery.addEventListener("change", sync);

        resize();
        step(0, false); draw();
        requestAnimationFrame(() => stage.classList.add("ready"));
    }

    // Lazy-load once the stage is near the viewport (or shortly after load).
    let booted = false;
    function boot() {
        if (booted) return;
        booted = true;
        if (!hasWebGL()) { stage.hidden = true; return; }
        loadThree().then(init).catch(() => { stage.hidden = true; });
    }
    if ("IntersectionObserver" in window) {
        const io = new IntersectionObserver((en) => {
            if (en.some(e => e.isIntersecting)) { io.disconnect(); boot(); }
        }, { rootMargin: "200px" });
        io.observe(stage);
    }
    setTimeout(() => { if (stage.offsetParent !== null) boot(); }, 1500);
})();
