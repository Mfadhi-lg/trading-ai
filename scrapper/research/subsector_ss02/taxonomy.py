"""Granular business taxonomy proposals grounded in retained activity descriptions.

Broad sectors, generic NBFCs, generic pharma and generic capital goods deliberately
have no completion rule. Patterns propose activities, not historical tutor decisions.
"""
import re
from functools import lru_cache

# code | proposed business subsector | literal/regex business activity phrases
CATALOG = r'''
PRIVATE_BANKS|PRIVATE SECTOR BANKING|private sector bank
PUBLIC_BANKS|PUBLIC SECTOR BANKING|public sector bank|government.owned bank
SMALL_FINANCE_BANKS|SMALL FINANCE BANKING|small finance bank
HOUSING_FINANCE|HOUSING MORTGAGE FINANCE|housing finance|home loans|housing loans|mortgage financ
MICROFINANCE|MICROFINANCE LENDING|microfinanc|micro.finance|micro.credit
GOLD_LOANS|GOLD COLLATERAL LENDING|gold loans|gold loan|loans against gold
VEHICLE_FINANCE|VEHICLE FINANCE|vehicle financ|commercial vehicle loans|automobile financ
CONSUMER_CREDIT|CONSUMER AND PERSONAL CREDIT|consumer financ|personal loans|consumer loans
INFRA_FINANCE|INFRASTRUCTURE PROJECT FINANCE|infrastructure financ|power financ|rural electrification financ
STOCK_BROKING|SECURITIES BROKING|stock.?brok|securities brok|equity brok
STOCK_EXCHANGES|SECURITIES EXCHANGES|stock exchange|securities exchange
COMMODITY_EXCHANGES|COMMODITY EXCHANGES|commodity exchange
DEPOSITORIES|SECURITIES DEPOSITORIES|depository services|securities depository
REGISTRARS|REGISTRAR AND INVESTOR SERVICING|registrar and transfer|registrar.*share transfer|investor servic|registrar to an issue
ASSET_MANAGEMENT|MUTUAL FUND ASSET MANAGEMENT|asset management company|mutual fund management|managing mutual funds
WEALTH_MANAGEMENT|WEALTH AND PORTFOLIO MANAGEMENT|wealth management|portfolio management services
INVESTMENT_BANKING|INVESTMENT BANKING|investment banking|merchant banking
LIFE_INSURANCE|LIFE INSURANCE UNDERWRITING|life insurance company|life insurer|life insurance business
GENERAL_INSURANCE|GENERAL INSURANCE UNDERWRITING|general insurance|non.life insurance
HEALTH_INSURANCE|HEALTH INSURANCE UNDERWRITING|standalone health insur|health insurance company
INSURANCE_BROKING|INSURANCE DISTRIBUTION|insurance brok|insurance distribution
CREDIT_RATING|CREDIT RATING SERVICES|credit rating|rating agency|rating agencies
PAYMENT_PROCESSING|DIGITAL PAYMENT PROCESSING|payment processing|payment solutions|payment gateway
TWO_WHEELERS|TWO AND THREE WHEELER MANUFACTURING|two.wheeler|2/3 wheelers|three.wheeler
PASSENGER_VEHICLES|PASSENGER VEHICLE MANUFACTURING|passenger cars|passenger vehicles|passenger car|utility vehicles
COMMERCIAL_VEHICLES|COMMERCIAL VEHICLE MANUFACTURING|commercial vehicle manufact|commercial vehicles including|manufactur.*buses|manufactur.*trucks
TRACTORS|AGRICULTURAL TRACTOR MANUFACTURING|tractor manufact|manufactur.*tractors
AUTO_LIGHTING|AUTOMOTIVE LIGHTING|automotive lighting|automobile lighting|vehicle lighting
AUTO_HARNESSES|AUTOMOTIVE WIRING HARNESSES|wiring harness
AUTO_BRAKES|AUTOMOTIVE BRAKING SYSTEMS|braking systems|brake systems|brake lining|brake assemblies
AUTO_AXLES|AUTOMOTIVE AXLES AND SUSPENSION|trailer axles|trailer axle|axle assemblies|suspension systems|axle shafts
AUTO_TRANSMISSION|AUTOMOTIVE TRANSMISSION SYSTEMS|transmission systems|clutch|automotive gears
AUTO_SEATING|AUTOMOTIVE SEATING|automotive seating|seating systems|seat covers
AUTO_PISTONS|AUTOMOTIVE PISTONS AND RINGS|pistons|piston rings
AUTO_FORGINGS|AUTOMOTIVE AND ENGINEERING FORGINGS|forgings|forged components|forged parts
AUTO_DIECAST|ALUMINIUM DIE CASTINGS|die.cast|diecast
AUTO_STAMPINGS|AUTOMOTIVE METAL STAMPINGS|automotive stamp|sheet metal components
TYRES|TYRE MANUFACTURING|tyre manufact|manufactur.*tyres|tyres and tubes|tires and tubes
BATTERY_LEAD|LEAD ACID BATTERIES|lead.acid batter
BATTERY_LITHIUM|LITHIUM BATTERIES AND STORAGE|lithium.ion batter|lithium battery
API|ACTIVE PHARMACEUTICAL INGREDIENTS|active pharmaceutical ingredients|active pharmaceutical ingredient|bulk drugs|pure.play api|api manufact
FORMULATIONS|PHARMACEUTICAL FORMULATIONS|pharmaceutical formulations|finished dosage|finished formulations|generic formulations
BIOPHARMA|BIOPHARMACEUTICALS AND VACCINES|vaccines|biosimilars|biopharmaceutical
PHARMA_CDMO|PHARMACEUTICAL CONTRACT DEVELOPMENT AND MANUFACTURING|contract development and manufactur|pharmaceutical cdmo|crams
PHARMA_INTERMEDIATES|PHARMACEUTICAL INTERMEDIATES|pharmaceutical intermediates|pharma intermediates|drug intermediates
CLINICAL_RESEARCH|CLINICAL RESEARCH SERVICES|clinical research|clinical trials|contract research organi
DIAGNOSTICS|DIAGNOSTIC LABORATORY SERVICES|diagnostic services|diagnostic chain|pathology|diagnostic laboratories
HOSPITALS|HOSPITAL OPERATIONS|hospital chain|operates.*hospitals|chain of hospitals|multispecialty hospital|multi.specialty hospital|hospital network
DIALYSIS|DIALYSIS SERVICES|dialysis
MEDICAL_DEVICES|MEDICAL DEVICE MANUFACTURING|medical device|surgical instruments|medical equipment manufact
PHARMA_DISTRIBUTION|PHARMACEUTICAL DISTRIBUTION|pharmaceutical distribut|distribution of pharmaceutical|pharma distribut
AGRO_TECHNICALS|AGROCHEMICAL TECHNICALS|agrochemical technical|technical grade pesticides|pesticide technical
PESTICIDE_FORMULATION|CROP PROTECTION FORMULATIONS|pesticide formulations|insecticides|herbicides|fungicides
DYES_PIGMENTS|DYES AND PIGMENTS|dyes|pigments|dyestuff
FLUOROCHEMICALS|FLUOROCHEMICALS AND REFRIGERANTS|fluorochem|refrigerant gases|fluoropolymers
CHLORALKALI|CHLOR ALKALI CHEMICALS|chlor.alkali|caustic soda|chlorine
ALKYL_AMINES|ALKYL AMINES|alkyl amines|aliphatic amines|methylamines|ethylamines
SURFACTANTS|SURFACTANTS AND PERSONAL CARE INGREDIENTS|surfactants
FLAVOURS_FRAGRANCES|FLAVOUR AND FRAGRANCE INGREDIENTS|flavour.*fragrance|flavor.*fragrance|aroma chemicals
CATALYSTS|SPECIALTY CATALYSTS AND STRUCTURE DIRECTING AGENTS|phase transfer catalysts|structure directing agents|specialty catalysts
INDUSTRIAL_GASES|INDUSTRIAL GASES|industrial gases|industrial gas manufact
CARBON_BLACK|CARBON BLACK|carbon black
GRAPHITE|GRAPHITE ELECTRODES|graphite electrodes
CARBON_MATERIALS|CALCINED COKE AND CARBON MATERIALS|calcined petroleum coke|coal tar pitch|calcined coke
EXPLOSIVES|INDUSTRIAL AND DEFENCE EXPLOSIVES|explosives|industrial detonator
ADHESIVES|ADHESIVES AND SEALANTS|adhesives|sealants
RESINS|SYNTHETIC RESINS|synthetic resins|epoxy resins|phenolic resins
OLEOCHEMICALS|OLEOCHEMICALS AND FATTY ACIDS|oleochem|fatty acids|fatty alcohols
CHEMICAL_DISTRIBUTION|CHEMICAL DISTRIBUTION|chemical distribut|distribution of chemicals|trading.*chemicals
UREA_FERTILIZER|UREA FERTILIZERS|urea fertil|manufactur.*urea
PHOSPHATIC_FERTILIZER|PHOSPHATIC AND COMPLEX FERTILIZERS|phosphatic fertil|complex fertil|diammonium phosphate|di.ammonium phosphate|npk fertil
BIO_FERTILIZER|BIO FERTILIZERS|bio.fertil|organic fertil
EMS|ELECTRONICS CONTRACT MANUFACTURING|electronics manufacturing services|electronic manufacturing services|electronics contract manufact|electronic contract manufact
PCB|PRINTED CIRCUIT BOARDS|printed circuit boards|printed circuit board|pcbs
SEMICONDUCTOR_DESIGN|SEMICONDUCTOR DESIGN SERVICES|semiconductor design|chip design|vlsi design
ELECTRONICS_DISTRIBUTION|ELECTRONIC COMPONENT DISTRIBUTION|electronic component distribut|distribut.*electronic components
SMART_METERS|SMART AND ELECTRICITY METERS|smart meter|electricity meter|energy meters
TELECOM_EQUIPMENT|TELECOM NETWORK EQUIPMENT|telecom equipment|telecommunication equipment|telecom network equipment
DEFENCE_SIMULATION|DEFENCE TRAINING AND SIMULATION|training simulators|combat training|defence simulation|military simulators
DEFENCE_ELECTRONICS|DEFENCE ELECTRONICS|defence electronics|defense electronics|electronic warfare|radar systems
AIRCRAFT|AIRCRAFT AND HELICOPTER MANUFACTURING|aircraft manufact|helicopter manufact|manufactur.*aircraft and helicopters
AEROSPACE_COMPONENTS|AEROSPACE ENGINE AND STRUCTURAL COMPONENTS|aero.engine components|aerospace components|aircraft components|aerostructures
GPU_CLOUD|GPU AND CLOUD COMPUTE INFRASTRUCTURE|gpu cloud|cloud gpu|cloud computing infrastructure|cloud infrastructure provider
SERVER_SYSTEMS|HIGH PERFORMANCE SERVER SYSTEMS|high.performance computing|server manufact|servers and storage
TRANSFORMERS|POWER TRANSFORMERS|power transformers|transformer manufact|manufactur.*transformers|transformers and rectifiers
POWER_CABLES|POWER AND ELECTRICAL CABLES|power cables|electrical cables|wires and cables|cables and wires
WINDING_WIRES|WINDING AND MAGNET WIRES|winding wires|winding wire|magnet wire|enamelled copper wire
CONDUCTORS|POWER TRANSMISSION CONDUCTORS|power transmission conductors|aluminium conductors|aluminum conductors|conductors.*cables|manufactur.*conductors
SPECIALTY_OILS|TRANSFORMER AND SPECIALTY OILS|transformer oils|specialty oils|speciality oils
INDUSTRIAL_MOTORS|INDUSTRIAL ELECTRIC MOTORS|electric motors|industrial motors
PUMPS|PUMPS AND PUMPING SYSTEMS|pumps|pumping systems
COMPRESSORS|INDUSTRIAL COMPRESSORS|air compressors|industrial compressors|compressor manufact
BOILERS|BOILERS AND PROCESS HEAT EQUIPMENT|boilers|heat recovery
HEAT_EXCHANGERS|HVAC HEAT EXCHANGERS|heat exchangers|heat exchanger
PRECISION_ENGINEERING|PRECISION ENGINEERED COMPONENTS|precision.engineer|precision components|precision machined|precision manufacturing
FLOW_PRODUCTS|METALLIC FLEXIBLE FLOW PRODUCTS|metallic flexible|stainless steel hoses|corrugated hoses|flexible flow|metal bellows
VALVES|INDUSTRIAL VALVES|industrial valves|valve manufact|manufactur.*valves
CUTTING_TOOLS|INDUSTRIAL CUTTING TOOLS|cutting tools|metal cutting tools
BEARINGS|INDUSTRIAL AND AUTOMOTIVE BEARINGS|bearings
ABRASIVES|INDUSTRIAL ABRASIVES|abrasives|grinding wheels
TEXTILE_MACHINERY|TEXTILE SPINNING MACHINERY|textile machinery|spinning machinery
EXTRUSION_MACHINERY|PLASTIC EXTRUSION MACHINERY|plastic extrusion|plastics machinery|extrusion machinery
MATERIAL_HANDLING|MATERIAL HANDLING EQUIPMENT|material handling equipment|cranes|forklifts
CONSTRUCTION_EQUIPMENT|CONSTRUCTION EQUIPMENT|construction equipment|earthmoving equipment|excavators
WELDING|WELDING EQUIPMENT AND CONSUMABLES|welding consumables|welding equipment|welding electrodes
INDUSTRIAL_AUTOMATION|INDUSTRIAL AUTOMATION AND CONTROLS|industrial automation|process control|control instruments
THERMAL_POWER|THERMAL POWER GENERATION|thermal power generat|coal.based power|thermal power plant
RENEWABLE_POWER|RENEWABLE POWER GENERATION|renewable power generat|renewable energy generat|solar power generat|wind power generat
HYDRO_POWER|HYDROELECTRIC POWER GENERATION|hydroelectric|hydro power
POWER_TRANSMISSION|POWER TRANSMISSION UTILITIES|power transmission company|power transmission utility|transmission of power|transmission of electricity
POWER_DISTRIBUTION|POWER DISTRIBUTION UTILITIES|power distribution company|electricity distribution|distribution of electricity
SOLAR_MODULES|SOLAR CELL AND MODULE MANUFACTURING|solar pv modules|solar modules|photovoltaic modules|solar cells
SOLAR_EPC|SOLAR EPC AND INSTALLATION|solar epc|solar engineering procurement|solar power projects.*epc
WIND_TURBINES|WIND TURBINE MANUFACTURING|wind turbine|wind energy equipment
OIL_GAS_EP|OIL AND GAS EXPLORATION AND PRODUCTION|oil.*gas exploration|oil exploration|upstream oil|hydrocarbon exploration
REFINERIES|PETROLEUM REFINING|oil refiner|petroleum refiner|refineries & marketing
GAS_DISTRIBUTION|CITY GAS DISTRIBUTION|city gas distribution|piped natural gas|compressed natural gas
GAS_PIPELINES|GAS TRANSMISSION PIPELINES|gas transmission|natural gas pipeline
LUBRICANTS|LUBRICATING OILS|lubricant|lubricating oils
STEEL_MAKING|STEEL MANUFACTURING|iron and steel|steel manufact|manufactur.*steel products|special.*steel|steel making|integrated steel
STEEL_TUBES|STEEL TUBES AND PIPES|steel tubes|steel pipes|stainless steel tubes|stainless steel pipes
STEEL_CASTINGS|STEEL AND IRON CASTINGS|steel castings|iron castings|ductile iron castings
FERRO_ALLOYS|FERRO ALLOY SMELTING|ferro.?alloys|ferro chrome|ferro manganese
ALUMINIUM|ALUMINIUM SMELTING AND ROLLING|aluminium manufact|aluminum manufact|aluminium products|aluminium smelt|aluminium sheets
ZINC|ZINC AND LEAD SMELTING|zinc.*lead|zinc smelt
COPPER|COPPER SMELTING AND PROCESSING|copper smelt|copper products|copper cathodes
COAL_MINING|COAL MINING|coal mining|coal miner|coal production
IRON_ORE_MINING|IRON ORE MINING|iron ore mining|iron ore producer
STONE_MINING|STONE AND MINERAL QUARRYING|mining.*granite|mining.*marble|stone quarry
COTTON_YARN|COTTON YARN SPINNING|cotton yarn|cotton spinning
SYNTHETIC_YARN|SYNTHETIC FIBRES AND YARN|polyester yarn|polyester staple|polyester filament|synthetic yarn|man.made fibres
FABRIC_WEAVING|FABRIC WEAVING AND PROCESSING|woven fabrics|weaving|fabric processing|denim fabric
KNIT_GARMENTS|KNITWEAR AND GARMENT MANUFACTURING|knitted garments|readymade garments|ready.made garments|garment manufact|knitted fabric
HOME_TEXTILES|HOME TEXTILE PRODUCTS|home textile|terry towels|bed linen|bed sheets
TECHNICAL_TEXTILES|TECHNICAL TEXTILES|technical textile|industrial fabrics|tyre cord|tire cord
JUTE|JUTE TEXTILE PRODUCTS|jute
WOOL|WOOL AND WORSTED TEXTILES|woollen|worsted|woolen
DAIRY|DAIRY PROCESSING AND PRODUCTS|dairy products|milk products|dairy processing|dairy business
EDIBLE_OILS|EDIBLE OIL REFINING AND PRODUCTS|edible oil|edible oils
SUGAR|SUGAR MILLING|sugar manufact|sugar mill|manufactur.*sugar
RICE_MILLING|RICE PROCESSING AND EXPORTS|basmati rice|rice milling|rice processing
TEA|TEA PLANTATIONS AND PROCESSING|tea plantation|tea manufact|tea production
COFFEE|COFFEE PROCESSING|instant coffee|coffee manufact|coffee processing
BAKERY|BISCUITS AND BAKERY PRODUCTS|biscuits|bakery products|baked goods
SNACKS|PACKAGED SNACK FOODS|snack foods|namkeen|snack manufact
CONFECTIONERY|CONFECTIONERY AND CHOCOLATE|confectionery|chocolates
SPICES|SPICE PROCESSING|spices|spice processing
ANIMAL_FEED|ANIMAL AND AQUACULTURE FEED|animal feed|poultry feed|aqua feed|aquaculture feed
AQUACULTURE|SHRIMP FARMING AND PROCESSING|shrimp|prawn|aquaculture
SPIRITS|DISTILLED SPIRITS|indian.made foreign liquor|distilled spirits|whisky|whiskey
COUNTRY_LIQUOR|COUNTRY LIQUOR|country liquor
BEER|BEER BREWING|beer|brewery|breweries
ETHANOL|ETHANOL DISTILLATION|ethanol|rectified spirit|extra neutral alcohol
TOBACCO|TOBACCO PRODUCTS|tobacco|cigarettes
PERSONAL_CARE|PERSONAL CARE PRODUCTS|personal care products|hair care|skin care|cosmetic manufact
HYGIENE|ABSORBENT HYGIENE PRODUCTS|sanitary napkins|diapers|adult incontinence
CONDOMS|CONDOM AND CONTRACEPTIVE MANUFACTURING|condoms|contraceptives
DETERGENTS|DETERGENTS AND CLEANING PRODUCTS|detergents|cleaning products
PAINTS|PAINTS AND COATINGS|paints|industrial coatings|decorative coatings
FURNITURE|FURNITURE MANUFACTURING|furniture manufact|manufactur.*furniture|moulded furniture
FANS|ELECTRIC FANS|electric fans|ceiling fans|manufactur.*fans
HOME_APPLIANCES|HOUSEHOLD APPLIANCES|home appliances|household appliances|kitchen appliances
LIGHTING|LED AND ELECTRICAL LIGHTING|led lighting|lighting products|lighting solutions
FOOTWEAR|FOOTWEAR MANUFACTURING|footwear|shoe manufact
APPAREL_RETAIL|APPAREL RETAIL|apparel retail|fashion retail|retail.*garments
JEWELLERY_RETAIL|JEWELLERY RETAIL|jewellery retail|jewelry retail|retail.*jewellery|retail.*jewelry
JEWELLERY_MANUFACTURING|JEWELLERY MANUFACTURING|jewellery manufact|jewelry manufact|manufactur.*jewellery|manufactur.*jewelry
DIAMONDS|DIAMOND CUTTING AND POLISHING|diamond cutting|polished diamonds|cut and polished
CEMENT|CEMENT MANUFACTURING|cement manufact|manufactur.*cement|cement & cement products
CERAMICS|CERAMIC AND VITRIFIED TILES|ceramic tiles|vitrified tiles|tile manufact
PLYWOOD|PLYWOOD MANUFACTURING|plywood
LAMINATES|DECORATIVE LAMINATES|decorative laminates|laminate manufact
MDF|MDF AND PARTICLE BOARDS|medium.density fibre|medium.density fiber|mdf|particle board
GLASS|FLAT AND ARCHITECTURAL GLASS|float glass|flat glass|architectural glass
BATHWARE|SANITARYWARE|sanitaryware|sanitary ware
PLASTIC_PIPES|PLASTIC PIPES AND FITTINGS|plastic pipes|pvc pipes|cpvc pipes|upvc pipes|plastic piping
FIBRE_CEMENT|FIBRE CEMENT BUILDING PRODUCTS|fibre cement|fiber cement|asbestos cement
FLEXIBLE_PACKAGING|FLEXIBLE PACKAGING|flexible packaging|flexible plastic packaging
POLYESTER_FILMS|POLYESTER AND SPECIALTY FILMS|polyester films|polyester film|bopet|chip to film|chip.to.film|specialty films
RIGID_PACKAGING|RIGID PLASTIC PACKAGING|rigid packaging|plastic packaging containers
GLASS_PACKAGING|GLASS CONTAINER PACKAGING|glass bottles|glass containers|container glass
METAL_PACKAGING|METAL PACKAGING|metal packaging|metal containers|tin containers
PAPERBOARD|PAPERBOARD AND PAPER PACKAGING|paperboard|paper packaging|kraft paper|corrugated boxes
WRITING_PAPER|WRITING AND PRINTING PAPER|writing and printing paper|printing and writing paper
PET_RECYCLING|PET AND PLASTIC RECYCLING|pet recycling|plastic recycling|recycled polyester
EWASTE|ELECTRONIC WASTE RECYCLING|e.waste|electronic waste
WATER_TREATMENT|WATER AND WASTEWATER TREATMENT|water treatment|wastewater|waste water|effluent treatment
MILITARY_SHIPBUILDING|NAVAL SHIPBUILDING|warships|naval ship|submarines|defence shipbuilding
SHIPBUILDING|COMMERCIAL SHIPBUILDING|shipbuilding|ship building|ship repair
MARITIME_SHIPPING|MARITIME FREIGHT SHIPPING|shipping company|shipping services|shipping business|cargo ships|bulk carriers|oil tankers
OFFSHORE_SERVICES|OFFSHORE OILFIELD SERVICES|offshore services|offshore support|offshore drilling
PORTS|PORT OPERATIONS|port operations|port & port services|port operator|operates.*ports
LOGISTICS_3PL|CONTRACT AND THIRD PARTY LOGISTICS|third.party logistics|integrated logistics|supply chain management
EXPRESS_LOGISTICS|EXPRESS PARCEL AND COURIER|express logistics|courier|express parcel
ROAD_FREIGHT|ROAD FREIGHT TRANSPORT|road transport|road freight|surface transport
AIR_CARGO|AIR CARGO SERVICES|air cargo|air freight
AIRLINES|PASSENGER AIRLINES|passenger airline|airline operator|airline company
RAIL_ROLLING_STOCK|RAILWAY ROLLING STOCK|rolling stock|railway wagons|rail wagons|rail coaches
RAIL_SIGNALLING|RAILWAY SIGNALLING AND SAFETY|railway signalling|railway signaling|train collision|kavach
RAIL_INFRA|RAILWAY INFRASTRUCTURE EPC|railway infrastructure|railway projects|railway electrification
ROAD_EPC|ROAD AND HIGHWAY EPC|road construction|highway construction|roads and highways|roads.*highways
BUILDING_EPC|BUILDING AND CIVIL CONSTRUCTION|civil construction|construction of buildings|building construction
POWER_EPC|POWER TRANSMISSION EPC|power transmission.*epc|transmission lines|transmission towers
RESIDENTIAL_REALTY|RESIDENTIAL PROPERTY DEVELOPMENT|residential real estate|residential projects|residential develop|housing projects
COMMERCIAL_REALTY|COMMERCIAL PROPERTY DEVELOPMENT|commercial real estate|commercial properties|office parks|commercial projects
HOTELS|HOTEL OPERATIONS|hotel chain|operates.*hotels|hotel business|hotel operations
RESTAURANTS|RESTAURANT AND QUICK SERVICE CHAINS|restaurant|quick.service restaurant
TRAVEL_OTA|ONLINE TRAVEL BOOKING|online travel|travel booking|online travel agency
TRAVEL_B2B|B2B TRAVEL DISTRIBUTION|travel distribution|b2b travel
TRAVEL_SAAS|TRAVEL AND HOSPITALITY SOFTWARE|travel.*saas|hospitality.*saas|travel technology
IT_SERVICES|IT SOFTWARE CONSULTING SERVICES|software development services|software services|it services|software consulting|it consulting|software outsourcing
IT_INFRA|IT INFRASTRUCTURE SERVICES|it infrastructure services|it infrastructure management
CYBERSECURITY|CYBERSECURITY SOFTWARE AND SERVICES|cybersecurity|cyber.security|digital security
BANKING_SOFTWARE|BANKING AND FINANCIAL SOFTWARE|banking software|financial software|core banking
ERP_SOFTWARE|ENTERPRISE RESOURCE PLANNING SOFTWARE|enterprise resource planning|erp software|enterprise software products
HR_SOFTWARE|HR AND PAYROLL SOFTWARE|payroll software|hr software|human capital management software
BPM|BUSINESS PROCESS MANAGEMENT SERVICES|business process management|business process outsourcing|bpo services
INTERNET_CLASSIFIEDS|ONLINE CLASSIFIEDS PLATFORMS|online classifieds|online recruitment|recruitment classifieds
E_RETAIL|ECOMMERCE MARKETPLACES|e.commerce platform|ecommerce marketplace|e.retail
ADVERTISING|ADVERTISING AND MARKETING SERVICES|advertising|digital marketing
TV_BROADCAST|TELEVISION BROADCASTING|television broadcast|tv broadcast|satellite channels
FILM_PRODUCTION|FILM AND TELEVISION PRODUCTION|film production|motion pictures|television content
MUSIC_RIGHTS|MUSIC INTELLECTUAL PROPERTY|music rights|music label|music production|music licensing
CINEMA|CINEMA EXHIBITION|multiplex|cinema exhibition|cinema theatres
PRINT_MEDIA|NEWSPAPER AND PRINT PUBLISHING|newspaper|print publishing|printing and publishing
EDUCATION|EDUCATION AND TRAINING SERVICES|education services|educational services|vocational training|coaching
STAFFING|STAFFING AND WORKFORCE SERVICES|staffing|manpower services|human resource services
SECURITY_SERVICES|SECURITY GUARDING SERVICES|security guarding|security guards|private security services
TESTING_INSPECTION|TESTING INSPECTION AND CERTIFICATION|testing.*inspection.*certification|testing and certification
AGRI_SEEDS|AGRICULTURAL SEEDS|hybrid seeds|seed production|agricultural seeds
AGRI_TRADING|AGRICULTURAL COMMODITY TRADING|agricultural commodities|agro commodities|trading.*agricultural
METAL_TRADING|METALS TRADING|trading.*metals|metal trading|trading.*steel|trading.*iron
TEXTILE_TRADING|TEXTILE AND APPAREL TRADING|trading.*textiles|trading.*fabrics|trading.*garments
ELECTRICAL_DISTRIBUTION|ELECTRICAL PRODUCT DISTRIBUTION|distribution.*electrical products|trading.*electrical
MINING_EQUIPMENT|UNDERGROUND AND OPENCAST MINING EQUIPMENT|equipment for underground|opencast mines|mining equipment|mining machinery
MF_DISTRIBUTION|MUTUAL FUND DISTRIBUTION|mutual fund distribut
LOCAL_SEARCH|LOCAL SEARCH AND BUSINESS DIRECTORY PLATFORMS|local search engine|local search.related|business directory
GROCERY_RETAIL|GROCERY AND SUPERMARKET RETAIL|supermarket chain|grocery retail|food and grocery|hypermarkets
SSP_FERTILIZER|SINGLE SUPER PHOSPHATE FERTILIZERS|single super phosphate|single superphosphate
MASTERBATCHES|PLASTIC MASTERBATCHES|masterbatches|masterbatch
CAST_IRON_PIPES|DUCTILE IRON PIPES|ductile iron pipe|di pipes
SEAMLESS_PIPES|SEAMLESS STEEL PIPES|seamless pipes|seamless tubes
WELDED_PIPES|WELDED STEEL PIPES|lsaw pipes|hsaw pipes|erw pipes|erw tubes
COATED_METALS|COATED AND GALVANIZED METAL PRODUCTS|coated metal|galvanized steel|galvanised steel|pre.painted steel
ENGINEERING_RD|ENGINEERING DESIGN AND R AND D SERVICES|engineering and technology services|engineering services company|design and technology services|engineering research.*development|engineering design services
CABLE_TV|CABLE TELEVISION DISTRIBUTION|cable tv|cable television
BROADBAND|FIXED BROADBAND INTERNET SERVICES|broadband|internet service provider
WHEEL_RIMS|AUTOMOTIVE WHEELS AND RIMS|wheel rims|alloy wheels|steel wheels
HORTICULTURE|HORTICULTURAL CROP PRODUCTION|horticulture crops|horticultural|floriculture
REFRACTORIES|INDUSTRIAL REFRACTORIES|ramming mass|refractories|refractory products|refractory materials
CLOCKS|CLOCKS AND TIMEKEEPING PRODUCTS|clocks|clock movements|horological
PETROLEUM_PRODUCTS|PETROLEUM PRODUCT MARKETING|distribution of petroleum products|marketing of petroleum products|petroleum marketing
POWER_PLANT_EQUIPMENT|POWER PLANT EQUIPMENT MANUFACTURING|power plant equipment|steam turbines|gas turbines
SATELLITE_COMMS|SATELLITE COMMUNICATION SERVICES|satellite communication
IRRIGATION|MICRO IRRIGATION SYSTEMS|micro irrigation|drip irrigation|drip and sprinkler
FLEX_WORKSPACE|FLEXIBLE WORKSPACE OPERATIONS|flexible workspace|co.working|coworking
PRIMARY_DEALERS|GOVERNMENT SECURITIES PRIMARY DEALERSHIP|primary dealer|government borrowing program|government securities issuances
CONTAINER_LOGISTICS|CONTAINER FREIGHT STATIONS AND DEPOTS|container freight station|inland container depot
VINYL_SURFACES|VINYL FLOORING AND SYNTHETIC LEATHER|vinyl flooring|pvc sheeting|artificial leather|synthetic leather
ENGINEERING_POLYMERS|ENGINEERING POLYMER RESINS|abs resins|asa resins|aes resins|san resins|engineering plastics
TEXTILE_CHEMICALS|TEXTILE PROCESSING CHEMICALS|textile specialty chemicals|textile speciality chemicals|textile chemicals
ONCOLOGY_FORMULATIONS|ONCOLOGY DRUG FORMULATIONS|oncology.*drugs|anti.cancer.*drugs|oncology formulations
INJECTABLES|INJECTABLE DRUG FORMULATIONS|injectable formulations|complex injectables|sterile injectables
HOSPITALITY_FINANCE|HOSPITALITY PROJECT FINANCE|financial assistance to the tourism|hospitality financ|tourism financ
FLEX_CREDIT|LEASE AND HIRE PURCHASE FINANCE|hire purchase|hire.purchase|leasing financ
TRACTOR_PARTS|TRACTOR AND FARM EQUIPMENT COMPONENTS|tractor components|tractor parts|farm equipment components
RUBBER_PRODUCTS|INDUSTRIAL RUBBER PRODUCTS|industrial rubber|rubber moulded|rubber molded|rubber components
PET_BOTTLES|PET PREFORMS AND BOTTLES|pet preforms|pet bottles
PLASTIC_MOULDINGS|PRECISION PLASTIC MOULDINGS|plastic injection mould|plastic injection mold|precision plastic|injection moulded|injection molded
LEATHER_PROCESSING|LEATHER TANNING AND PROCESSING|leather processing|leather tanning|tanning of leather|finished leather
LUGGAGE|LUGGAGE AND BAGS|luggage|travel bags|backpacks
SANITARY_PAPER|TISSUE AND HYGIENE PAPER|tissue paper|hygiene paper
PRINTING|COMMERCIAL PRINTING SERVICES|commercial printing|printing services|printing solutions
PRINT_CYLINDERS|PRINTING CYLINDERS AND ENGRAVING|rotogravure|printing cylinders
RETAIL_MERCHANDISE|DEPARTMENT AND GENERAL MERCHANDISE RETAIL|department store|general merchandise retail|retail chain of|value retail
TOYS|TOY MANUFACTURING|toys|toy manufact
SPORTS_GOODS|SPORTS GOODS MANUFACTURING|sports goods|sports equipment
OPTICAL_RETAIL|OPTICAL AND EYEWEAR RETAIL|eyewear|optical retail|prescription glasses
ANIMAL_HEALTH|VETERINARY PHARMACEUTICALS|veterinary|animal health products
PROTEIN_INGREDIENTS|GELATIN AND PROTEIN INGREDIENTS|gelatine|gelatin|collagen
NUTRACEUTICALS|NUTRACEUTICAL INGREDIENTS AND PRODUCTS|nutraceutical|dietary supplements
INDUSTRIAL_ENZYMES|INDUSTRIAL ENZYMES|enzymes|biocatalysts
MINERAL_PROCESSING|MINERAL BENEFICIATION AND PROCESSING|mineral beneficiation|mineral processing|iron ore pellets|pellet manufact
STEEL_ROLLING|STEEL REROLLING AND LONG PRODUCTS|tmt bars|steel billets|steel rolling|steel reroll|hot rolled|cold rolled
FASTENERS|INDUSTRIAL AND AUTOMOTIVE FASTENERS|fasteners|nuts and bolts|high.tensile bolts
SPRINGS|INDUSTRIAL AND AUTOMOTIVE SPRINGS|leaf springs|coil springs|spring manufact
HYDRAULICS|HYDRAULIC AND PNEUMATIC SYSTEMS|hydraulic systems|pneumatic systems|hydraulic cylinders
WEIGHING|WEIGHING AND MEASUREMENT EQUIPMENT|weighing|weighbridges
SENSORS|INDUSTRIAL SENSORS AND INSTRUMENTATION|sensors|instrumentation|measuring instruments
FIRE_SAFETY|FIRE PROTECTION EQUIPMENT|fire protection|fire fighting|fire detection
UPS|POWER BACKUP AND UPS SYSTEMS|uninterruptible power|ups systems|power backup
GENERATORS|DIESEL GENERATOR SETS|diesel generator|gensets|generator sets
SOLVENT_EXTRACTION|SOLVENT EXTRACTION AND OILSEED PROCESSING|solvent extraction|oilseed processing|oil seeds processing
PULSES|PULSES AND GRAIN PROCESSING|pulses|dal mill|grain processing|flour mill
SALT|INDUSTRIAL AND EDIBLE SALT|salt manufact|manufactur.*salt|edible salt
SEAFOOD|SEAFOOD PROCESSING|seafood|fish processing|frozen fish
POTABLE_WATER|PACKAGED DRINKING WATER|packaged drinking water|mineral water
SOFT_DRINKS|CARBONATED AND NON ALCOHOLIC BEVERAGES|carbonated beverages|soft drinks|bottling.*beverages
FOOD_INGREDIENTS|FOOD INGREDIENTS AND ADDITIVES|food ingredients|food additives|starch|sweeteners
EVENTS|EVENT AND EXHIBITION SERVICES|event management|exhibitions|exhibition management
FACILITY_MANAGEMENT|FACILITY MANAGEMENT SERVICES|facility management|facilities management|housekeeping services
BUSINESS_SUPPORT|BUSINESS SUPPORT AND OUTSOURCING SERVICES|business support services|back office services
'''


CATALOG += r'''
PAYMENTS_BANKS|PAYMENTS BANKING|payments bank
MORTGAGE_PLATFORMS|MORTGAGE SERVICING TECHNOLOGY|mortgage servicing|mortgage technology
SECURITIES_DISTRIBUTION|FINANCIAL PRODUCT DISTRIBUTION|financial product distribut|capital market products distribut
RETAIL_BROKING|RETAIL SECURITIES BROKING|broking services.*capital markets|broking company|share and stock brokers|stock broker
REFINING_CRUDE|CRUDE OIL REFINING|refining crude oil|crude oil refiner
SOLAR_CELLS|SOLAR CELL AND MODULE SYSTEMS|solar pv module|photovoltaic.*cell manufact
AIR_CONDITIONING|AIR CONDITIONER MANUFACTURING|room air conditioners|air.conditioner manufact|manufactur.*air.condition
TUBE_PACKAGING|LAMINATED TUBE PACKAGING|laminated plastic tubes|laminated tubes|tube packaging
WATER_PURIFICATION|CONSUMER WATER PURIFICATION|water purification|water purifiers
AIR_PURIFICATION|CONSUMER AIR PURIFICATION|air purification|air purifiers
ALUMINIUM_PANELS|ALUMINIUM COMPOSITE PANELS|aluminium composite panels|aluminum composite panels
EDUCATION_SOFTWARE|EDUCATIONAL SOFTWARE AND ASSESSMENTS|digital learning|online assessment solutions|education software
EV_CHARGING|ELECTRIC VEHICLE CHARGING SYSTEMS|ev chargers|electric vehicle charging|ev charging
SOFTWARE_TESTING|SOFTWARE QUALITY ASSURANCE SERVICES|software validation|software verification|software testing
SOCKS|SOCKS AND HOSIERY MANUFACTURING|socks|hosiery
MOTHER_BABY_RETAIL|MOTHER AND CHILD PRODUCT RETAIL|products for mothers.*babies|baby products retail|baby and kids
MEDICAL_IMAGING|MEDICAL IMAGING EQUIPMENT|medical.imaging equipment|mri manufact|diagnostic imaging equipment
WRITING_INSTRUMENTS|WRITING INSTRUMENTS|writing instruments|pens and pencils
COMMERCIAL_LEASING|COMMERCIAL PROPERTY LEASING|leasing out of commercial|commercial property leas|renting.*commercial propert
LED_LUMINAIRES|LED LUMINAIRES AND FIXTURES|led lights|lighting.*fixtures|led fixtures
HOSPITAL_FACILITIES|HOSPITAL AND CLINICAL FACILITIES|comprise hospitals|hospital facilities|healthcare facilities
METALLURGICAL_CONSUMABLES|FOUNDRY ADDITIVES AND CONSUMABLES|metallurgical industry.*additives|foundry consumables|foundry chemicals
RAIL_SPRINGS|RAILWAY SUSPENSION SPRINGS|springs.*wagon|springs.*locomotive|l.h.b. springs
SPECIALTY_INTERMEDIATES|SPECIALTY CHEMICAL INTERMEDIATES|advanced intermediates|specialty chemical intermediates|speciality chemical intermediates
BIOMASS_POWER|BIOMASS AND COGENERATION POWER|biomass power|co.generation|cogeneration
AGRI_EQUIPMENT|AGRICULTURAL IMPLEMENTS|agricultural implements|farm implements|agricultural equipment
DIAMOND_JEWELLERY|DIAMOND JEWELLERY|diamond.studded jewellery|diamond jewellery
GOLD_REFINING|PRECIOUS METAL REFINING|gold refining|precious metal refin
GEMSTONE_PROCESSING|GEMSTONE PROCESSING|gemstones|gemstone processing
STONE_PROCESSING|GRANITE AND MARBLE PROCESSING|granite slabs|marble slabs|processing.*granite|processing.*marble
DISTILLERY|ALCOHOL DISTILLERY PRODUCTS|distillery|distilleries
RESORTS|RESORT AND VACATION OWNERSHIP|vacation ownership|holiday resorts|resort operations
TOLL_ROADS|TOLL ROAD OPERATIONS|toll roads|toll collection|toll plazas|toll bridge
EPC_WATER|WATER INFRASTRUCTURE EPC|water infrastructure|water supply projects|irrigation projects
EPC_OIL_GAS|OIL AND GAS ENGINEERING SERVICES|oil.*gas.*engineering.*procurement|petroleum.*engineering consultancy
DRILLING|OILFIELD DRILLING SERVICES|drilling services|oilfield services|drilling rigs
FREIGHT_FORWARDING|INTERNATIONAL FREIGHT FORWARDING|freight forwarding|freight forwarder
MARINE_CHARTERING|VESSEL CHARTERING|fleet operating and chartering|vessel chartering|ship chartering
WAREHOUSING|WAREHOUSING OPERATIONS|warehousing services|warehouse operator|warehousing business
RUBBER_CHEMICALS|RUBBER PROCESSING CHEMICALS|rubber chemicals|rubber processing chemicals
PERFORMANCE_CHEMICALS|WATER TREATMENT CHEMICALS|water treatment chemicals|water-treatment chemicals
CASTOR_DERIVATIVES|CASTOR OIL AND DERIVATIVES|castor oil|castor derivatives
SODA_ASH|SODA ASH AND INORGANIC ALKALIS|soda ash
SULPHURIC_ACID|SULPHURIC ACID|sulphuric acid|sulfuric acid
ACETIC_CHEMICALS|ACETYL AND ACETIC ACID CHEMICALS|acetic acid|acetic anhydride|ethyl acetate
PLASTIC_FLEXIBLES|PLASTIC FILM EXTRUSION|plastic films|polypropylene films|bopp films
OPTICAL_FIBRE|OPTICAL FIBRE AND TELECOM CABLES|optical fibre|optical fiber|telecom cables|fiber optic|fibre optic
''' 

CATALOG += r'''
PROPERTY_DEVELOPMENT|PROPERTY DEVELOPMENT|real estate development|property development
YARN_SPINNING|YARN SPINNING|manufactur.*yarn|yarn manufact|spinning of yarn|spinning mills
INVESTMENT_HOLDING|FINANCIAL INVESTMENT HOLDING|investment company|core investment company|investing.*securities|investments in shares|investing.*shares
PROPERTY_SERVICES|REAL ESTATE AGENCY AND SERVICES|real estate services|property brokerage|real estate agents
PRINTING_INKS|PRINTING INKS|printing inks
TELECOM_TOWERS|TELECOM TOWER INFRASTRUCTURE|telecom towers|tower infrastructure
CELLULAR_TELECOM|CELLULAR TELECOM NETWORKS|mobile telecom|cellular services|cellular telecommunication
SPONGE_IRON|SPONGE IRON PRODUCTION|sponge iron|direct reduced iron
PIG_IRON|PIG IRON PRODUCTION|pig iron
OIL_GAS_TRADING|GAS TRADING AND MARKETING|trading of gas|trading.*natural gas
ELECTRICITY_GENERATION|ELECTRICITY GENERATION OPERATORS|electricity generation|power generation
APPAREL_PRODUCTS|APPAREL PRODUCTS|apparel products
ENGINEERED_WOOD_PANELS|ENGINEERED WOOD PANELS|engineered wood panels
AUTO_DEALERS|AUTOMOTIVE DEALERSHIPS|automobile dealer|automotive dealer
AIRPORTS|AIRPORT OPERATIONS|airport operator|airport operations
DREDGING|MARINE DREDGING SERVICES|dredging
POWER_TRADING|ELECTRICITY TRADING|power trading|electricity trading
PETROLEUM_STORAGE|PETROLEUM STORAGE AND TERMINALS|oil storage|petroleum storage
PHARMACY_RETAIL|RETAIL PHARMACIES|pharmacy retail|retail pharmacy|retail pharmacies
CYCLING|BICYCLES AND COMPONENTS|bicycles|bicycle components
MEAT_PROCESSING|MEAT AND POULTRY PROCESSING|meat processing|poultry processing
'''

@lru_cache(maxsize=1)
def definitions():
    rows=[]
    for line in CATALOG.strip().splitlines():
        if not line.strip():continue
        code,label,*patterns=line.split('|')
        rows.append(dict(code=code,label=label,patterns=patterns,is_granular=True,
                         origin='independently_proposed_business_activity_taxonomy'))
    return rows


@lru_cache(maxsize=1)
def compiled():
    return [(rule,[(p,re.compile(r'(?<![a-z])(?:'+p+r')[a-z]*(?![a-z])')) for p in rule['patterns']]) for rule in definitions()]


def activities(text):
    normalized=text.casefold().replace('–','-').replace('’',"'")
    found=[]
    for rule,patterns in compiled():
        matches=[]
        for pattern,expression in patterns:
            for match in expression.finditer(normalized):
                # Customer use, future plans and former activities do not establish an exposure.
                context=normalized[max(0,match.start()-65):match.start()]
                if re.search(r'(used in|used for|customers in|clients in|plans to|intends to|proposes to|formerly|previously|ceased)[^.;]{0,60}$',context):
                    continue
                matches.append(dict(start=match.start(),end=match.end(),matched_text=text[match.start():match.end()],pattern=pattern))
        if matches:found.append(dict(code=rule['code'],matches=matches))
    # A narrower directly documented activity supersedes a same-product umbrella only.
    codes={r['code'] for r in found}
    suppress=set()
    if 'MILITARY_SHIPBUILDING' in codes:suppress.add('SHIPBUILDING')
    if 'SMALL_FINANCE_BANKS' in codes:suppress.add('PRIVATE_BANKS')
    if codes & {'COTTON_YARN','SYNTHETIC_YARN'}:suppress.add('YARN_SPINNING')
    if codes & {'RESIDENTIAL_REALTY','COMMERCIAL_REALTY'}:suppress.add('PROPERTY_DEVELOPMENT')
    return [r for r in found if r['code'] not in suppress]


# Only these exact historical Basic Industry descriptors are granular enough to count.
HISTORICAL_INDUSTRY={
 'Private Sector Bank':'PRIVATE_BANKS','Public Sector Bank':'PUBLIC_BANKS',
 '2/3 Wheelers':'TWO_WHEELERS','Passenger Cars & Utility Vehicles':'PASSENGER_VEHICLES',
 'Commercial Vehicles':'COMMERCIAL_VEHICLES','Hospital':'HOSPITALS',
 'Asset Management Company':'ASSET_MANAGEMENT','Life Insurance':'LIFE_INSURANCE',
 'General Insurance':'GENERAL_INSURANCE','Housing Finance Company':'HOUSING_FINANCE',
 'Port & Port services':'PORTS','Oil Exploration & Production':'OIL_GAS_EP',
 'Refineries & Marketing':'REFINERIES','Coal':'COAL_MINING','Iron & Steel':'STEEL_MAKING',
 'Aluminium':'ALUMINIUM','Zinc':'ZINC','Personal Care':'PERSONAL_CARE',
 'Civil Construction':'BUILDING_EPC','Paints':'PAINTS','Computers - Software & Consulting':'IT_SERVICES',
 'Power - Transmission':'POWER_TRANSMISSION','Power Distribution':'POWER_DISTRIBUTION',
 'Power Generation':'ELECTRICITY_GENERATION',
 'Residential, Commercial Projects':'PROPERTY_DEVELOPMENT',
 'Airline':'AIRLINES','Internet & Catalogue Retail':None,
 'Ship Building & Allied Services':None,'Gas Transmission/Marketing':None,
 'Breweries & Distilleries':None, 'Pharmaceuticals':None,'Heavy Electrical Equipment':None,
 'Specialty Chemicals':None,'Auto Components & Equipments':None,
}

# Explicit fine-business allowlist for CURRENT official BSE descriptors. Everything
# else remains broad/ambiguous unless the business description supports a finer activity.
OFFICIAL_BUSINESS={**{k:v for k,v in HISTORICAL_INDUSTRY.items() if v},
 'Sugar':'SUGAR','Stockbroking & Allied':'STOCK_BROKING',
 'Business Process Outsourcing (BPO)/ Knowledge Process Outsourcing (KPO)':'BPM',
 'Household Appliances':'HOME_APPLIANCES','Trading - Metals':'METAL_TRADING',
 'Trading - Chemicals':'CHEMICAL_DISTRIBUTION','Cigarettes & Tobacco Products':'TOBACCO',
 'Edible Oil':'EDIBLE_OILS','Dyes And Pigments':'DYES_PIGMENTS',
 'Restaurants':'RESTAURANTS','Carbon Black':'CARBON_BLACK','Explosives':'EXPLOSIVES',
 'Cables - Electricals':'POWER_CABLES','Industrial Gases':'INDUSTRIAL_GASES',
 'Offshore Support Solution Drilling':'OFFSHORE_SERVICES','Dairy Products':'DAIRY',
 'Business Process Outsourcing (BPO)/Knowledge Process Outsourcing (KPO)':'BPM',
 'Trading - Gas':'OIL_GAS_TRADING','Water Supply & Management':'WATER_TREATMENT',
 'Real Estate related services':'PROPERTY_SERVICES','Footwear':'FOOTWEAR',
 'Telecom - Infrastructure':'TELECOM_TOWERS','Printing Inks':'PRINTING_INKS',
 'Copper':'COPPER','Airline':'AIRLINES','Tractors':'TRACTORS','Seafood':'SEAFOOD',
 'Sponge Iron':'SPONGE_IRON','Pig Iron':'PIG_IRON',
 'Residential, Commercial Projects':'PROPERTY_DEVELOPMENT','Investment Company':'INVESTMENT_HOLDING',
 'Stock Exchanges':'STOCK_EXCHANGES','Depositories':'DEPOSITORIES',
 'Credit Rating Agencies':'CREDIT_RATING',
 'Hotels & Resorts':'HOTELS','Logistics Solution Provider':'LOGISTICS_3PL',
 'Garments & Apparels':'APPAREL_PRODUCTS','Telecom - Equipment & Accessories':'TELECOM_EQUIPMENT',
 'Plywood Boards/ Laminates':'ENGINEERED_WOOD_PANELS','E-Retail/ E-Commerce':'E_RETAIL',
 'Construction Vehicles':'CONSTRUCTION_EQUIPMENT','Jute & Jute Products':'JUTE',
 'Print Media':'PRINT_MEDIA','Ferro & Silica Manganese':'FERRO_ALLOYS',
 'Trading - Textile Products':'TEXTILE_TRADING','Road Transport':'ROAD_FREIGHT',
 'Lubricants':'LUBRICANTS','Road Assets–Toll, Annuity, Hybrid-Annuity':'TOLL_ROADS',
 'Advertising & Media Agencies':'ADVERTISING','Microfinance Institutions':'MICROFINANCE',
 'Animal Feed':'ANIMAL_FEED','Ratings':'CREDIT_RATING','Pharmacy Retail':'PHARMACY_RETAIL',
 'Dredging':'DREDGING','Oil Storage & Transportation':'PETROLEUM_STORAGE',
 'Insurance Distributors':'INSURANCE_BROKING','Power Trading':'POWER_TRADING',
 'Auto -Dealer':'AUTO_DEALERS','Airport & Airport services':'AIRPORTS',
 'Railway Wagons':'RAIL_ROLLING_STOCK','Sanitary Ware':'BATHWARE','Cycles':'CYCLING',
 'Meat Products including Poultry':'MEAT_PROCESSING',
}
